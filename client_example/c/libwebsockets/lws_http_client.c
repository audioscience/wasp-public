
/**********************************************
(C) Copyright AudioScience Inc. 2020
***********************************************/

#include "wasp_interface.h"
#include "lws_http_client.h"
#include <libwebsockets.h>

static int in_progress = 0;
static struct wasp_if_msg msg = { 0 };
static struct lws_client_connect_info ci;
static struct lws_context *context = NULL;
static struct wasp_if_msg u_i[WASP_IF_MAX_DEVICES + 1] = { 0 };
static char type[WASP_IF_OBJ_TYPE_LEN];
static char prop[WASP_IF_OBJ_PROP_LEN];
static char key[WASP_IF_UPDATE_STREAM_BODY_KEY_LEN];
static char val[WASP_IF_UPDATE_STREAM_BODY_VAL_LEN];
static char path[WASP_IF_PATH_LEN];
static char update_body[WASP_IF_BODY_LEN];

void lws_http_client_send(struct lws_context *context, struct wasp_if_msg *msg)
{
	int dev_index = 0;
	struct wasp_if_msg * ui = NULL;
	if (strcmp(msg->path, "/wasp/u2/objects")) {
		in_progress = 1; /* flag for maintaining only one active GET/PATCH/POST request at a time */
		ui = &u_i[0];
	} else {
		dev_index = _wasp_if_ipv4_to_device_index(msg->ipv4_address);
		if (dev_index == -1) {
			/* device not found */
			return;
		}

		dev_index += 1;
		ui = &u_i[dev_index];
	}

	/* store the path and method for later lookup within callback */
	strncpy(ui->path, msg->path, sizeof(msg->path));
	strncpy(ui->method, msg->method, sizeof(msg->method));
	strncpy(ui->ipv4_address, msg->ipv4_address, sizeof(msg->ipv4_address));

	ci.protocol = "http";
	ci.port = 80;
	ci.address = msg->ipv4_address;
	ci.method = msg->method;
	ci.path = msg->path;
	ci.context = context;
	ci.opaque_user_data = ui;
	if (strcmp(msg->body, "")) {
		int len = strlen(msg->body);
		strncpy(&ui->body[LWS_PRE], msg->body, len);
		ui->body_len = len;
	}

	//printf("%s %s %s %s\n", ci.address, ci.method, ci.path, msg->body);
	lws_client_connect_via_info(&ci);
}

void send_authorization_request(void)
{
	struct wasp_if_msg tmp_msg;
	_wasp_if_msg_init(
		&tmp_msg,
		"POST",
		msg.ipv4_address,
		"/wasp/r2/device/auth",
		NULL);
	lws_http_client_send(context, &tmp_msg);
}

static int lws_callback_http(struct lws *wsi, enum lws_callback_reasons reason, void *user, void *in, size_t len)
{
	/* mjson_next() args */
	int koff, klen, voff, vlen, vtype;

	const char *body;
	int body_len;
	int ret;

	struct wasp_if_msg *ui = (struct wasp_if_msg *)lws_get_opaque_user_data(wsi);

	switch (reason) {
	/* connection established */
	case LWS_CALLBACK_ESTABLISHED_CLIENT_HTTP:
	{
		ui->pos = 0;
		break;
	}
	/* connection error */
	case LWS_CALLBACK_CLIENT_CONNECTION_ERROR:
	{
		printf("Unable to connect to device at address %s\n", ui->ipv4_address);
		break;
	}
	/* add custom headers as required */
	case LWS_CALLBACK_CLIENT_APPEND_HANDSHAKE_HEADER:
	{
		const char *auth_str = NULL;
		unsigned char **p = (unsigned char **)in;
		unsigned char *end = (*p) + len;

		/* authorization request (POST) */
		if (!strcmp(ui->method, "POST")) {
			if (lws_add_http_header_by_name(wsi,
				(const unsigned char *)"Authorization:",
				(const unsigned char *)"Hawk", 4, p, end)) {
				return -1;
			}
		} else {

			/* PATCH requests */
			if (!strcmp(ui->method, "PATCH")) {

				/* Content Length */
				if (lws_add_http_header_by_name(wsi,
					(const unsigned char *)"Content-Length:",
					(const unsigned char *)"256", 3, p, end)) {
					return -1;
				}

				/* Content Type */
				if (lws_add_http_header_by_name(wsi,
					(const unsigned char *)"Content-Type:",
					(const unsigned char *)"application/json", 16, p, end)) {
					return -1;
				}

				/* notify a write (PATCH request content) is pending */
				lws_client_http_body_pending(wsi, 1);
				lws_callback_on_writable(wsi);
			}


			auth_str = _wasp_if_ipv4_to_auth_str(ui->ipv4_address);
			if (auth_str) {
				/* All GET/PATCH requests, add authorization header */
				if (lws_add_http_header_by_name(wsi,
					(const unsigned char *)"Authorization:",
					(const unsigned char *)auth_str, WASP_IF_AUTH_STR_LEN, p, end)) {
					return -1;
				}
			}
		}
		break;
	}
	case LWS_CALLBACK_RECEIVE_CLIENT_HTTP_READ:
	{
		const char *p = in;
		char auth_id[WASP_IF_AUTH_ID_LEN];
		char auth_id_str[WASP_IF_AUTH_STR_LEN];

		/* POST authorization request */
		if (!strcmp(ui->method, "POST") && !strcmp(ui->path, "/wasp/r2/device/auth")) {
			/* store the authorization ID */
			json_get_string(p, strlen(p), "$.id", auth_id, WASP_IF_AUTH_ID_LEN);
			sprintf(auth_id_str, "Hawk id=\"%s\"", auth_id);
			_wasp_if_store_auth_str(ui->ipv4_address, auth_id_str);

		}

		/* GET requests */
		if (!strcmp(ui->method, "GET")) {

			/* update stream */
			if (!strcmp(ui->path, "/wasp/u2/objects")) {

				char *in = (char*)p;
				char *delim = "---";
				char *token;

				do {
					token = strstr(in, delim);
					if (token) {
						*token = '\0';
					}

					/* get the update type */
					json_get_string(in, strlen(in), "$._type", type, sizeof(type));
					/* store pointer to update body */
					json_find(in, strlen(in), "$.body", &body, &body_len);
					/* update:group_prefix - multiple objects of common type, property */
					if (!strcmp(type, "update:group_prefix")) {
						/* get the property common to the group */
						json_get_string(in, strlen(in), "$.prop", prop, sizeof(prop));

						/* iterate through each body element {{"id1":value1}, {"id2":value2}, ...} */
						ret = 0;
						while (1) {
							ret = json_next(body, body_len, ret, &koff, &klen, &voff, &vlen, &vtype);
							if (ret == 0) {
								break;
							}

							/* get the update body ID */
							strncpy(key, &body[koff + 1], klen - 1);
							key[klen - 2] = '\0';
							/* get the update body value */
							strncpy(val, &body[voff], vlen);
							val[vlen] = '\0';

							sprintf(path, "/objects/%d", atoi(key));
							sprintf(update_body, "{\"%s\":%s}", prop, val);
							_wasp_if_notify_update_stream_rcvd(ui->ipv4_address, path, update_body);
						}
					}
					/* update:obj - one object */
					if (!strcmp(type, "update:obj")) {
						json_get_string(in, strlen(in), "$.path", path, sizeof(path));
						ret = 0;
						/* iterate through each body element {{"prop1":value1}, {"prop1":value1}, ...} */
						while (1) {
							ret = json_next(body, body_len, ret, &koff, &klen, &voff, &vlen, &vtype);
							if (ret == 0) {
								break;
							}

							/* get the update body property */
							strncpy(key, &body[koff + 1], klen - 1);
							key[klen - 2] = '\0';
							/* get the update body property value */
							strncpy(val, &body[voff], vlen);
							val[vlen] = '\0';

							sprintf(update_body, "{\"%s\":%s}", key, val);
							_wasp_if_notify_update_stream_rcvd(ui->ipv4_address, path, update_body);
						}
					}

					in = token + strlen(delim);
				} while (token != NULL);
			}

			if (strstr(ui->path, "/wasp/r2/objects/") || !strcmp(ui->path, "/wasp/r2/device/info")) {
				_wasp_if_store_single_object(p);
			}

			/* store the schemas. lws does not appear to reassemble large fragmented responses (objects, schemas) - assemble ourselves */
			if (!strcmp(ui->path, "/wasp/r2/schemas")) {
				_wasp_if_store_schema(ui->ipv4_address, ui->pos, p, len);
				ui->pos += len;
			}
			/* store the objects - see above */
			if (!strcmp(ui->path, "/wasp/r2/objects")) {
				_wasp_if_store_object(ui->ipv4_address, ui->pos, p, len);
				ui->pos += len;
			}
		}

		return 0;
	}
	/* callback for writing request payload */
	case LWS_CALLBACK_CLIENT_HTTP_WRITEABLE:
	{
		if (lws_http_is_redirected_to_get(wsi))
			break;

		/* write PATCH payload data */
		lws_write(wsi, (unsigned char*)&ui->body[LWS_PRE], ui->body_len, LWS_WRITE_HTTP_FINAL);
		lws_client_http_body_pending(wsi, 0);

		return 0;
	}
	/* read established (precedes LWS_CALLBACK_RECEIVE_CLIENT_HTTP_READ callbacks) */
	case LWS_CALLBACK_RECEIVE_CLIENT_HTTP:
	{
		char buffer[WASP_IF_RESP_BUF_LEN + LWS_PRE];
		char *px = buffer + LWS_PRE;
		int lenx = sizeof(buffer) - LWS_PRE;

		if (lws_http_client_read(wsi, &px, &lenx) < 0) {
			return -1;
		}

		return 0;
	}
	/* request completed */
	case LWS_CALLBACK_COMPLETED_CLIENT_HTTP:
	{
		/* get the status code */
		int last_err_code = lws_http_client_http_response(wsi);
		_wasp_if_store_last_err_code(last_err_code);

		/* object update stream closed - reconnect... */
		if (!strcmp(ui->path, "/wasp/u2/objects")) {
			struct wasp_if_msg tmp_msg;
			_wasp_if_msg_init(
				&tmp_msg,
				"GET",
				ui->ipv4_address,
				"/wasp/u2/objects",
				NULL);
			lws_http_client_send(context, &tmp_msg);
		}

		if (last_err_code != 401) {
			if (strcmp(ui->path, "/wasp/r2/device/auth") && strcmp(ui->path, "/wasp/u2/objects")) {
				_wasp_if_notify_request_complete();
			}


		}
		in_progress = 0; /* clear in_progress flag to allow another request to be sent */
		lws_cancel_service(lws_get_context(wsi));
		break;
	}
	case LWS_CALLBACK_CLOSED_CLIENT_HTTP:
		lws_cancel_service(lws_get_context(wsi));
		break;

	default:
		break;
	}

	return lws_callback_http_dummy(wsi, reason, user, in, len);
}

static const struct lws_protocols protocols[] = {
	{
		"http",
		lws_callback_http,
		0,
		0,
		0,
		NULL,
		0
	},
	{ NULL, NULL, 0, 0, 0, NULL, 0 }
};

void * lws_http_client_thread(void *args)
{
	int n = 0;
	int resend = 0;

	(void)args; /* unused */

	/* create the LWS HTTP client context */
	struct lws_context_creation_info info;
	memset(&info, 0, sizeof info);
	info.protocols = protocols;
	context = lws_create_context(&info);
	if (!context) {
		printf("ERROR: lws init failed\n");
		return NULL;
	}

	while (n >= 0) {
		/* one request allowed at a time */
		if (!in_progress) {
			/* permissions error, send the authorization request */
			if (_wasp_if_get_last_err_code() == 401) {
				send_authorization_request();
				resend = 1;
			} else {
				if (resend) {
					/* resend the last (401 failed) request */
					lws_http_client_send(context, &msg);
					resend = 0;
				} else {
					/* read and send requests */
					if (_wasp_if_msg_read(&msg)) {
						lws_http_client_send(context, &msg);
					}
				}
			}
		}
		n = lws_service(context, 0);
	}

	lws_context_destroy(context);

	return NULL;
}

