
/**********************************************
(C) Copyright AudioScience Inc. 2020
***********************************************/

#ifndef _WASP_INTERFACE_H
#define _WASP_INTERFACE_H

#include <stdlib.h>
#include "json.h"

#define WASP_IF_MAX_DEVICES 32      /* maximum number of WASP devices supported by this interface */
#define WASP_IF_METHOD_LEN 16
#define WASP_IF_IPV4_ADDRESS_LEN 16
#define WASP_IF_OBJ_TYPE_LEN 128
#define WASP_IF_OBJ_PROP_LEN 128
#define WASP_IF_UPDATE_STREAM_BODY_KEY_LEN 64
#define WASP_IF_UPDATE_STREAM_BODY_VAL_LEN 64
#define WASP_IF_PATH_LEN 128
#define WASP_IF_BODY_LEN 1024
#define WASP_IF_RESP_BUF_LEN 1500
#define WASP_IF_AUTH_ID_LEN 65
#define WASP_IF_AUTH_STR_LEN 74
#define WASP_IF_SCHEMA_ID_MAX_LEN 128

typedef void (*async_cb_t)(const char *ipv4_address, const char *path, const char *update_body);

struct wasp_if_msg {
	char method[WASP_IF_METHOD_LEN];             /* GET, PATCH, or POST */
	char ipv4_address[WASP_IF_IPV4_ADDRESS_LEN]; /* dotted IPv4 device address */
	char path[WASP_IF_PATH_LEN];                 /* endpoint, e.g. /wasp/r2/objects/3 */
	char body[WASP_IF_BODY_LEN];                 /* PATCH content, e.g. {"active": false} */
	int body_len;
	int pos;
};

//////////////////////////////////////////////////////////////////////////////////

/**
 * Start the WASP HTTP client interface.
 *
 * /param u_cb - object update stream callback function
 *
 * /returns nonzero on error.
 */
int wasp_if_init(async_cb_t u_cb);

/**
 * Connect to a WASP device, store the objects/schemas and
 * optionally open a connection to the object update stream.
 *
 * /param device_index - 0 to (WASP_IF_MAX_DEVICES - 1)
 * /param ipv4_address - dotted IPv4 device address
 * /param enable_update_stream - (1) : open a connection to the object update stream
 *
 * /returns nonzero on error.
 */
int wasp_if_connect_to_device(
	unsigned int device_index,
	const char *ipv4_address,
	int enable_update_stream
);

/**
 * Look up the object ID associated with given parameters
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param obj_type - the WASP object type, e.g. "ctrl:mute"
 * /param obj_type_len - length of obj_type
 * /param io_type - the object I/O type, e.g. "analog".  NULL if N/A.
 * /param io_type_len - length of io_type, 0 if N/A
 * /param io_dir - the object I/O direction e.g. "out". NULL if N/A.
 * /param io_dir_len - length of io_dir, 0 if N/A
 * /param io_idx - the index of the I/O. -1 if N/A.
 *
 * /return the ID, -1 on error
 */
int wasp_if_get_obj_id(
	const char *ipv4_address,
	const char *obj_type,
	size_t obj_type_len,
	const char *io_type,
	size_t io_type_len,
	const char *io_dir,
	size_t io_dir_len,
	int io_idx
);

/**
 * Look up the object ID of a child control
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param obj_type - the control object type, e.g. "ctrl:phantom"
 * /param obj_type_len - length of obj_type
 * /param parent_id - the ID of the "block:io" parent of the control
 *
 * /return the ID, -1 on error
 */
int wasp_if_get_ctrl_id(
	const char *ipv4_address,
	const char *obj_type,
	size_t obj_type_len,
	int parent_id
);

/**
 * Get a string-type property of an object
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param obj_id - the ID of the object
 * /param prop_name - the name of the property
 * /param prop_name_len - length of prop_name
 * /param prop - allocated buffer to write the string into
 * /param prop_len - length of prop
 * /param cached - (1) - read from stored objects, (0) - fetch new info via GET /objects/[x]
 *
 * /return the HTTP status code if fetched via GET, (0), if read from stored objects successfully, (-1) on internal error
 */
int wasp_if_object_get_property_str(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	char *prop,
	size_t prop_len,
	int cached
);

/**
 * Get a number-type property of an object
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param obj_id - the ID of the object
 * /param prop_name - the name of the property
 * /param prop_name_len - length of prop_name
 * /param prop - int pointer to write the number to
 * /param cached - (1) - read from stored objects, (0) - fetch new info via GET /objects/[x]
 *
 * /return the HTTP status code if fetched via GET, (0), if read from stored objects successfully, (-1) on internal error
 */
int wasp_if_object_get_property_num(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	int *prop,
	int cached
);

/**
 * Get a boolean-type property of an object
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param obj_id - the ID of the object
 * /param prop_name - the name of the property
 * /param prop_name_len - length of prop_name
 * /param prop - int pointer to write the boolean to
 * /param cached - (1) - read from stored objects, (0) - fetch new info via GET /objects/[x]
 *
 * /return the HTTP status code if fetched via GET, (0), if read from stored objects successfully, (-1) on internal error
 */
int wasp_if_object_get_property_bool(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	int *prop,
	int cached
);

/**
 * Set a number-type property of an object
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param obj_id - the ID of the object
 * /param prop_name - the name of the property
 * /param prop_name_len - length of prop_name
 * /param val - the new value of the property
 *
 * /return the HTTP status code, -1 on internal error
 */
int wasp_if_object_set_property_num(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	int val);

/**
 * Set a boolean-type property of an object
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param obj_id - the ID of the object
 * /param prop_name - the name of the property
 * /param prop_name_len - length of prop_name
 * /param state - the new state of the property
 *
 * /return the HTTP status code, -1 on internal error
 */
int wasp_if_object_set_property_bool(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	int state
);

/**
 * Set a string-type property of an object
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param obj_id - the ID of the object
 * /param prop_name - the name of the property
 * /param prop_name_len - length of prop_name
 * /param val - the new value of the property
 * /param prop_name_len - length of val
 *
 * /return the HTTP status code, -1 on internal error
 */
int wasp_if_object_set_property_str(
	const char *ipv4_address,
	int obj_id,
	const char *prop_name,
	size_t prop_name_len,
	const char *val,
	size_t val_len
);

/**
 * Set multiple properties within an object in one request
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param update_body - the formatted JSON array properties, e.g. [{\"_id\":%d, \"active\":%s}, ...]
 * /param update_body_len - length of update_body
 *
 * /return the HTTP status code, -1 on internal error
 */
int wasp_if_object_set_multiple_properties(
	const char *ipv4_address,
	const char *update_body,
	size_t update_body_len
);

/**
 * Get a string-type property of an schema
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param schema_id - the ID of the schema
 * /param schema_id_len - length of schema_id
 * /param prop_name - the name of the property
 * /param prop_name_len - length of prop_name
 * /param prop - allocated buffer to write the string into
 * /param prop_len - length of prop
 *
 * /return non-zero on error
 */
int wasp_if_schema_get_property_str(
	const char *ipv4_address,
	const char *schema_id,
	size_t schema_id_len,
	const char *prop_name,
	size_t prop_name_len,
	char *prop,
	size_t prop_len
);

/**
 * Get a number-type property of an schema
 *
 * /param ipv4_address - the dotted IPv4 device address
 * /param schema_id - the ID of the schema
 * /param schema_id_len - length of schema_id
 * /param prop_name - the name of the property
 * /param prop_name_len - length of prop_name
 * /param prop - int pointer to write the value to
 *
 * /return non-zero on error
 */
int wasp_if_schema_get_property_num(
	const char *ipv4_address,
	const char *schema_id,
	size_t schema_id_len,
	const char *prop_name,
	size_t prop_name_len,
	int *prop
);

//////////////////////////////////////////////////////////////////////////////////

/**
 * Build a request message to queue from application to the HTTP client.
 *
 * /param msg - empty message to fill
 * /param method - GET, PATCH, or POST
 * /param ipv4_address - dotted IPv4 device address
 * /param path - the endpoint, e.g. /wasp/r2/objects/3
 * /param body - PATCH content, NULL if not applicable
 */
void _wasp_if_msg_init(
	struct wasp_if_msg *msg,
	const char *method,
	const char *ipv4_address,
	const char *path,
	const char *body
);

/**
 * Queue the request message
 *
 * /param msg - the message to write from
 */
int _wasp_if_msg_write(
	struct wasp_if_msg *msg
);


/**
 * Read the request message
 *
 * /param msg - the message to read into
 */
int _wasp_if_msg_read(
	struct wasp_if_msg *msg
);

const char * _wasp_if_ipv4_to_auth_str(const char *ipv4_address);
int _wasp_if_store_auth_str(const char *ipv4_address, const char *auth_str);
int _wasp_if_ipv4_to_device_index(const char *ipv4_address);
void _wasp_if_notify_objects_schemas_read(void);
void _wasp_if_notify_update_stream_rcvd(const char *ipv4_address, const char *path, const char *update_body);
void _wasp_if_store_schema(const char *ipv4_address, int pos, const char *buf, int len);
void _wasp_if_store_object(const char *ipv4_address, int pos, const char *buf, int len);
void _wasp_if_store_single_object(const char *buf);
int _wasp_if_get_last_err_code(void);
void _wasp_if_store_last_err_code(int code);
void _wasp_if_notify_request_complete(void);

#endif /*_WASP_INTERFACE_H */
