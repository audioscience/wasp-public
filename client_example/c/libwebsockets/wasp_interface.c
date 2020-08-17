
/**********************************************
(C) Copyright AudioScience Inc. 2020
***********************************************/

#include "wasp_interface.h"
#include "lws_http_client.h"

#include <signal.h>
#include <pthread.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include <errno.h>
#include <semaphore.h>

#define MAX_LEN 65536 /* 64 KB */

static char objects[WASP_IF_MAX_DEVICES][MAX_LEN];
static char schemas[WASP_IF_MAX_DEVICES][MAX_LEN];
static char devices[WASP_IF_MAX_DEVICES][WASP_IF_IPV4_ADDRESS_LEN] = { 0 };
static char wasp_auth_strs[WASP_IF_MAX_DEVICES][WASP_IF_AUTH_STR_LEN] = { 0 };

async_cb_t _u_cb = NULL;

static int fd[2];
static sem_t s_request_done;

static int last_err_code = 0;
static char resp_buffer[WASP_IF_RESP_BUF_LEN] = { 0 };
static struct wasp_if_msg msg = { 0 };

///////////////////////////////////////////////////////////////////////////////

int wasp_if_init(async_cb_t u_cb)
{
	_u_cb = u_cb;

	pipe(fd);
	if (fcntl(fd[0], F_SETFL, O_NONBLOCK) < 0) {
		printf("error setting up pipe\n");
		return -1;
	}

	sem_init(&s_request_done, 0, 0);
	pthread_t new_thread_id;
	pthread_create(&new_thread_id, NULL, lws_http_client_thread, NULL);

	return 0;
}

int wasp_if_connect_to_device(
	unsigned int device_index,
	const char *ipv4_address,
	int enable_update_stream
)
{
	if (device_index >= WASP_IF_MAX_DEVICES) {
		printf("Device index %d is greater than MAX_DEVICES (%d)\n",
			device_index,
			WASP_IF_MAX_DEVICES);
		return -1;
	}

	strncpy(devices[device_index], ipv4_address, WASP_IF_IPV4_ADDRESS_LEN-1);
	devices[device_index][WASP_IF_IPV4_ADDRESS_LEN-1] = '\0';

	/* wait for objects and schemas to be read upon startup of lws_http_client */
	printf("Connecting to %s and reading objects and schemas - can take several seconds...\n",
		ipv4_address);

	/* get all objects */
	_wasp_if_msg_init(
		&msg,
		"GET",
		ipv4_address,
		"/wasp/r2/objects",
		NULL);

	_wasp_if_msg_write(&msg);
	sem_wait(&s_request_done);

	/* get all schemas */
	_wasp_if_msg_init(
		&msg,
		"GET",
		ipv4_address,
		"/wasp/r2/schemas",
		NULL);

	_wasp_if_msg_write(&msg);
	sem_wait(&s_request_done);

	/* object update stream */
	if (enable_update_stream) {
		_wasp_if_msg_init(
			&msg,
			"GET",
			ipv4_address,
			"/wasp/u2/objects",
			NULL);

		_wasp_if_msg_write(&msg);
	}

	printf("Done\n");

	return 0;
}

int _wasp_if_msg_write(struct wasp_if_msg *msg)
{
	size_t bytes_left = sizeof(*msg);
	uint8_t *buf_ptr = (uint8_t *)msg;
	while (bytes_left > 0) {
		ssize_t retval = write(fd[1], buf_ptr, bytes_left);
		if (retval < 0) {
			printf("fail writing to pipe\n");
			return -1;
		}
		bytes_left -= retval;
		buf_ptr += retval;
	}

	return 0;
}

int _wasp_if_msg_read(struct wasp_if_msg *msg)
{
	ssize_t ret = read(fd[0], msg, sizeof(*msg));
	if (ret < 0) {
		if (errno != EAGAIN) {
			printf("read pipe error\n");
		}
	} else if (!ret) {
		printf("EOF reading from pipe\n");
	} else {
		return 1;
	}

	return 0;
}

void _wasp_if_msg_init(
	struct wasp_if_msg *msg,
	const char *method,
	const char *ipv4_address,
	const char *path,
	const char *body
)
{
	memset(msg, 0, sizeof(*msg));
	strncpy(msg->method, method, WASP_IF_METHOD_LEN-1);
	msg->method[WASP_IF_METHOD_LEN-1] = '\0';
	strncpy(msg->ipv4_address, ipv4_address, WASP_IF_IPV4_ADDRESS_LEN-1);
	msg->ipv4_address[WASP_IF_IPV4_ADDRESS_LEN-1] = '\0';
	strncpy(msg->path, path, WASP_IF_PATH_LEN-1);
	msg->path[WASP_IF_PATH_LEN-1] = '\0';
	if (body) {
		strncpy(msg->body, body, WASP_IF_BODY_LEN-1);
		msg->body[WASP_IF_BODY_LEN-1] = '\0';
	}
}

void _wasp_if_notify_update_stream_rcvd(
	const char *ipv4_address,
	const char *path,
	const char *update_body
)
{
	_u_cb(ipv4_address, path, update_body);
}

int _wasp_if_ipv4_to_device_index(const char *ipv4_address)
{
	int i = 0;
	for (i = 0; i < WASP_IF_MAX_DEVICES; i++) {
		if (!strcmp(ipv4_address, devices[i])) {
			return i;
		}
	}

	return -1;
}

void _wasp_if_store_schema(
	const char *ipv4_address,
	int pos,
	const char *buf,
	int len
)
{
	if ((strlen(buf) + pos + len) > MAX_LEN) {
		return;
	}

	char *schs = NULL;
	int index = _wasp_if_ipv4_to_device_index(ipv4_address);
	if (index == -1) {
		return;
	}

	schs = schemas[index];

	if (!pos) {
		memset(schs, 0, sizeof(*schs));
	}

	strncpy(&schs[pos], buf, len);
}

void _wasp_if_store_object(
	const char *ipv4_address,
	int pos,
	const char *buf,
	int len
)
{
	if ((strlen(buf) + pos + len) > MAX_LEN) {
		return;
	}

	char *objs = NULL;
	int index = _wasp_if_ipv4_to_device_index(ipv4_address);
	if (index == -1) {
		return;
	}

	objs = objects[index];

	if (!pos) {
		memset(objs, 0, sizeof(*objs));
	}

	strncpy(&objs[pos], buf, len);
}

void _wasp_if_store_single_object(const char *buf)
{
	memset(resp_buffer, 0, WASP_IF_RESP_BUF_LEN);
	strncpy(resp_buffer, buf, WASP_IF_RESP_BUF_LEN-1);
	resp_buffer[WASP_IF_RESP_BUF_LEN-1] = '\0';
}

int _wasp_if_get_last_err_code(void)
{
	return last_err_code;
}

void _wasp_if_notify_request_complete()
{
	sem_post(&s_request_done);
}

void _wasp_if_store_last_err_code(int code)
{
	last_err_code = code;
}

///////////////////////////////////////////////////////////////////////////////

int _wasp_if_object_set_property(
	const char *ipv4_address,
	int obj_id,
	const char *update_body,
	size_t update_body_len
)
{
	if (update_body_len > WASP_IF_BODY_LEN) {
		/* size validation */
		return -1;
	}

	char path[WASP_IF_PATH_LEN];
	snprintf(path, WASP_IF_PATH_LEN, "/wasp/r2/objects/%d", obj_id);
	_wasp_if_msg_init(&msg, "PATCH", ipv4_address, path, update_body);
	_wasp_if_msg_write(&msg);
	sem_wait(&s_request_done);

	return last_err_code;
}

int _wasp_if_object_get_property_obj(
	const char *ipv4_address,
	int obj_id,
	int cached,
	const char **object,
	int *object_len
)
{
	char buf[WASP_IF_BODY_LEN];
	double num;
	int koff, klen, voff, vlen, vtype;
	int ret = 0;
	int offset = 0;
	const char *objs = NULL;
	int index = 0;

	index = _wasp_if_ipv4_to_device_index(ipv4_address);
	if (index == -1) {
		return -1;
	}

	objs = objects[index];

	if (cached) {
		/* look up the property in the stored objects */
		while (1) {
			offset = json_next(objs, strlen(objs), offset, &koff, &klen, &voff, &vlen, &vtype);
			if (offset == 0) {
				break;
			}

			ret = json_get_number(&objs[voff], vlen, "$._id", &num);
			if (ret != 0) {
				if (obj_id != (int)num) {
					continue;
				}
			}

			*object = &objs[voff];
			*object_len = vlen;

			return 0;
		}
	} else {
		/* send a GET requst and wait on the response */
		snprintf(buf, WASP_IF_BODY_LEN, "/wasp/r2/objects/%d", obj_id);
		_wasp_if_msg_init(&msg, "GET", ipv4_address, buf, NULL);
		_wasp_if_msg_write(&msg);
		sem_wait(&s_request_done);
		*object = resp_buffer;
		*object_len = strlen(resp_buffer);

		return 0;
	}

	return -1;
}

const char * _wasp_if_ipv4_to_auth_str(const char *ipv4_address)
{
	int i = 0;
	for (i = 0; i < WASP_IF_MAX_DEVICES; i++) {
		if (!strcmp(ipv4_address, devices[i])) {
			return wasp_auth_strs[i];
		}
	}

	return NULL;
}

int _wasp_if_store_auth_str(const char *ipv4_address, const char *auth_str)
{
	int i = 0;
	for (i = 0; i < WASP_IF_MAX_DEVICES; i++) {
		if (!strcmp(ipv4_address, devices[i])) {
			strncpy(wasp_auth_strs[i], auth_str, WASP_IF_AUTH_STR_LEN);
			return 0;
		}
	}

	return -1;
}

///////////////////////////////////////////////////////////////////////////////

int wasp_if_get_obj_id(
	const char *ipv4_address,
	const char *obj_type,
	size_t obj_type_len,
	const char *io_type,
	size_t io_type_len,
	const char *io_dir,
	size_t io_dir_len,
	int io_idx)
{
	char buf[WASP_IF_BODY_LEN];
	double num;
	int koff, klen, voff, vlen, vtype;
	int ret = 0;
	int offset = 0;
	const char *objs = NULL;
	int index = 0;

	index = _wasp_if_ipv4_to_device_index(ipv4_address);
	if (index == -1) {
		/* device not found */
		return -1;
	}

	if (obj_type_len > WASP_IF_OBJ_TYPE_LEN ||
	    io_type_len > WASP_IF_OBJ_TYPE_LEN ||
	    io_dir_len > WASP_IF_OBJ_TYPE_LEN) {
		/* size validation */
		return -1;
	}

	objs = objects[index];

	while (1) {
		offset = json_next(objs, strlen(objs), offset, &koff, &klen, &voff, &vlen, &vtype);
		if (offset == 0) {
			break;
		}

		ret = json_get_string(&objs[voff], vlen, "$._type", buf, sizeof(buf));
		if (ret != -1) {
			if (strcmp(obj_type, buf)) {
				continue;
			}
		}

		if (io_type) {
			ret = json_get_string(&objs[voff], vlen, "$.io_type", buf, sizeof(buf));
			if (ret != -1) {
				if (strcmp(io_type, buf)) {
					continue;
				}
			}
		}


		if (io_dir) {
			ret = json_get_string(&objs[voff], vlen, "$.io_dir", buf, sizeof(buf));
			if (ret != -1) {
				if (strcmp(io_dir, buf)) {
					continue;
				}
			}
		}

		if (io_type && io_idx) {
			ret = json_get_number(&objs[voff], vlen, "$.io_idx", &num);
			if (ret != 0) {
				if (io_idx != (int)num) {
					continue;
				}
			}
		}

		ret = json_get_number(&objs[voff], vlen, "$._id", &num);
		if (ret != 0) {
			return (int)num;
		}
	}

	/* not found */
	return -1;
}

int wasp_if_get_ctrl_id(
	const char *ipv4_address,
	const char *obj_type,
	size_t obj_type_len,
	int parent_id
)
{
	char buf[WASP_IF_BODY_LEN];
	double num;
	int koff, klen, voff, vlen, vtype;
	int ret = 0;
	int offset = 0;
	const char *objs = NULL;
	int index = 0;

	index = _wasp_if_ipv4_to_device_index(ipv4_address);
	if (index == -1) {
		/* device not found */
		return -1;
	}

	if (obj_type_len > WASP_IF_OBJ_TYPE_LEN) {
		/* size validation */
		return -1;
	}

	objs = objects[index];

	while (1) {

		offset = json_next(objs, strlen(objs), offset, &koff, &klen, &voff, &vlen, &vtype);
		if (offset == 0) {
			break;
		}

		ret = json_get_string(&objs[voff], vlen, "$._type", buf, sizeof(buf));
		if (ret != -1) {
			if (strcmp(obj_type, buf)) {
				continue;
			}
		}

		ret = json_get_number(&objs[voff], vlen, "$._parent", &num);
		if (ret != 0) {
			if (parent_id != (int)num) {
				continue;
			}
		}

		ret = json_get_number(&objs[voff], vlen, "$._id", &num);
		if (ret != 0) {
			return (int)num;
		}
	}

	/* not found */
	return -1;
}

int wasp_if_object_get_property_str(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	char *prop,
	size_t prop_len,
	int cached
)
{
	int ret = 0;
	char buf[WASP_IF_BODY_LEN];
	int object_len = 0;
	const char *object;

	if (prop_name_len > WASP_IF_OBJ_PROP_LEN ||
	    prop_len > WASP_IF_OBJ_PROP_LEN) {
		/* size validation */
		return -1;
	}

	if (_wasp_if_object_get_property_obj(ipv4_address, obj_id, cached, &object, &object_len)) {
		/* error */
		return -1;
	}

	snprintf(buf, WASP_IF_BODY_LEN, "$.%s", prop_name);
	ret = json_get_string(object, object_len, buf, prop, prop_len);
	if (ret != -1) {
		return cached ? 0 : last_err_code;
	}

	/* not found */
	return -1;
}

int wasp_if_object_get_property_num(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	int *prop,
	int cached
)
{
	int ret = 0;
	double num = 0;
	char buf[WASP_IF_BODY_LEN];
	int object_len = 0;
	const char *object;

	if (prop_name_len > WASP_IF_OBJ_PROP_LEN) {
		/* size validation */
		return -1;
	}

	if (_wasp_if_object_get_property_obj(ipv4_address, obj_id, cached, &object, &object_len)) {
		/* error */
		return -1;
	}

	snprintf(buf, WASP_IF_BODY_LEN, "$.%s", prop_name);
	ret = json_get_number(object, object_len, buf, &num);
	if (ret != 0) {
		*prop = (int)num;
		return cached ? 0 : last_err_code;
	}

	/* not found */
	return -1;
}

int wasp_if_object_get_property_bool(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	int *prop,
	int cached
)
{
	int ret = 0;
	int boolean = 0;
	char buf[WASP_IF_BODY_LEN];
	int object_len = 0;
	const char *object;

	if (prop_name_len > WASP_IF_OBJ_PROP_LEN) {
		/* size validation */
		return -1;
	}

	if (_wasp_if_object_get_property_obj(ipv4_address, obj_id, cached, &object, &object_len)) {
		/* not found */
		return -1;
	}

	snprintf(buf, WASP_IF_BODY_LEN, "$.%s", prop_name);
	ret = json_get_bool(object, object_len, buf, &boolean);
	if (ret != 0) {
		*prop = (int)boolean;
		return cached ? 0 : last_err_code;
	}

	/* not found */
	return -1;
}

int wasp_if_schema_get_property_str(
	const char *ipv4_address,
	const char *schema_id,
	size_t schema_id_len,
	const char *prop_name,
	size_t prop_name_len,
	char *prop,
	size_t prop_len
)
{
	int ret = 0;
	char buf[WASP_IF_BODY_LEN];
	int index = 0;
	char *schs = NULL;
	index = _wasp_if_ipv4_to_device_index(ipv4_address);
	if (index == -1) {
		/* device not found */
		return -1;
	}

	if (schema_id_len > WASP_IF_OBJ_PROP_LEN ||
	    prop_name_len > WASP_IF_OBJ_PROP_LEN ||
	    prop_len > WASP_IF_OBJ_PROP_LEN) {
		/* size validation */
		return -1;
	}

	schs = schemas[index];

	snprintf(buf, WASP_IF_BODY_LEN, "$.%s.%s", schema_id, prop_name);
	ret = json_get_string(schs, strlen(schs), buf, prop, prop_len);
	if (ret != -1) {
		return 0;
	}

	/* not found */
	return -1;
}

int wasp_if_schema_get_property_num(
	const char *ipv4_address,
	const char *schema_id,
	size_t schema_id_len,
	const char *prop_name,
	size_t prop_name_len,
	int *prop
)
{
	int ret = 0;
	double num = 0;
	char buf[WASP_IF_BODY_LEN];
	int index = 0;
	char *schs = NULL;
	index = _wasp_if_ipv4_to_device_index(ipv4_address);
	if (index == -1) {
		/* device not found */
		return -1;
	}

	if (schema_id_len > WASP_IF_OBJ_PROP_LEN ||
	    prop_name_len > WASP_IF_OBJ_PROP_LEN) {
		/* size validation */
		return -1;
	}

	schs = schemas[index];

	snprintf(buf, WASP_IF_BODY_LEN, "$.%s.%s", schema_id, prop_name);
	ret = json_get_number(schs, strlen(schs), buf, &num);
	if (ret != -1) {
		*prop = num;
		return 0;
	}

	/* not found */
	return -1;
}


int wasp_if_object_set_property_num(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	int val
)
{
	char update_body[WASP_IF_BODY_LEN];

	if (prop_name_len > WASP_IF_OBJ_PROP_LEN) {
		/* size validation */
		return -1;
	}

	snprintf(update_body, WASP_IF_BODY_LEN, "{\"%s\": %d}", prop_name, val);

	return _wasp_if_object_set_property(ipv4_address, obj_id, update_body, sizeof(update_body));
}

int wasp_if_object_set_property_bool(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	int state
)
{
	char update_body[WASP_IF_BODY_LEN];

	if (prop_name_len > WASP_IF_OBJ_PROP_LEN) {
		/* size validation */
		return -1;
	}

	snprintf(update_body, WASP_IF_BODY_LEN, "{\"%s\": %s}", prop_name, state ? "true" : "false");

	return _wasp_if_object_set_property(ipv4_address, obj_id, update_body, sizeof(update_body));
}

int wasp_if_object_set_property_str(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	const char *val,
	size_t val_len
)
{
	char update_body[WASP_IF_BODY_LEN];

	if (prop_name_len > WASP_IF_OBJ_PROP_LEN ||
	    val_len > WASP_IF_OBJ_PROP_LEN) {
		/* size validation */
		return -1;
	}

	snprintf(update_body, WASP_IF_BODY_LEN, "{\"%s\": \"%s\"}", prop_name, val);

	return _wasp_if_object_set_property(ipv4_address, obj_id, update_body, sizeof(update_body));
}

int wasp_if_object_set_multiple_properties(
	const char *ipv4_address,
	const char *update_body,
	size_t update_body_len
)
{
	if (update_body_len > WASP_IF_BODY_LEN) {
		/* size validation */
		return -1;
	}

	_wasp_if_msg_init(&msg, "PATCH", ipv4_address, "/wasp/r2/objects", update_body);
	_wasp_if_msg_write(&msg);
	sem_wait(&s_request_done);

	return last_err_code;
}

