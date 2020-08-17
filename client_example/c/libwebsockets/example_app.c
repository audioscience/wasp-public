
/**********************************************
(C) Copyright AudioScience Inc. 2020
***********************************************/

#include "wasp_interface.h"
#include <string.h>
#include <stdio.h>
#include <unistd.h>

int analog_input(const char *ipv4_address, int index)
{
	int val = 0;
	int level_to_set = 10;
	int ret = 0;
	int parent_id = 0;
	int mute_ctrl_id = 0;
	int phantom_ctrl_id = 0;
	int level_ctrl_id = 0;
	char schema_id[WASP_IF_OBJ_PROP_LEN];
	char prop[WASP_IF_OBJ_PROP_LEN];
	char body[WASP_IF_BODY_LEN];
	char name[WASP_IF_OBJ_PROP_LEN];
	const char *label_prop = "label";
	const char *type = "block:io";
	const char *io_type = "analog";
	const char *io_dir = "in";
	const char *level_obj_type = "ctrl:trim_level";
	const char *level_level_prop = "level";
	const char *level_schema_prop = "_schema";
	const char *level_schema_units = "properties.level.wasp-unit";
	const char *level_schema_min = "properties.level.minimum";
	const char *level_schema_max = "properties.level.maximum";
	const char *mute_obj_type = "ctrl:mute";
	const char *mute_active_prop = "active";
	const char *phantom_ctrl_type = "ctrl:phantom";
	const char *phantom_active_prop = "active";

	printf("\n---look up the analog index %d IO block---\n", index);

	parent_id = wasp_if_get_obj_id(
		ipv4_address,
		type,
		strlen(type),
		io_type,
		strlen(io_type),
		io_dir,
		strlen(io_dir),
		index
	);
	if (parent_id == -1) {
		printf("cannot find analog IO block %d\n", index);
		return -1;
	}

	/* set a custom label for the IO block */
	printf("set a custom label for the IO block\n");
	snprintf(name,
		WASP_IF_OBJ_PROP_LEN,
		"Mic/Line %d",
		index+1
	);
	ret = wasp_if_object_set_property_str(
		ipv4_address,
		parent_id,
		label_prop,
		strlen(label_prop),
		name,
		sizeof(name)
	);
	if (ret == 200 || ret == 202) {
		printf("set IO block label success\n");
	} else {
		printf("set label fail, err: %d\n", ret);
	}

	printf("\n---find the child controls of this block---\n");

	printf("\n---level---\n");

	/* level */
	level_ctrl_id = wasp_if_get_ctrl_id(
		ipv4_address,
		level_obj_type,
		strlen(level_obj_type),
		parent_id
	);
	if (level_ctrl_id == -1) {
		printf("error finding trim level control ID\n");
		return -1;
	}

	/* read the level */
	ret = wasp_if_object_get_property_num(
		ipv4_address,
		level_ctrl_id,
		level_level_prop,
		strlen(level_level_prop),
		&val,
		1
	);
	if (ret == -1) {
		printf("error finding level object 'level' property\n");
		return -1;
	}

	printf("read the current level: %ddBu\n", val);

	/* write the level */
	printf("write level -> %ddBu\n", level_to_set);
	ret = wasp_if_object_set_property_num(
		ipv4_address,
		level_ctrl_id,
		level_level_prop,
		strlen(level_level_prop),
		level_to_set
	);
	if (ret == 200 || ret == 202) {
		printf("set level success\n");
	} else {
		printf("set level fail, err: %d\n", ret);
		return -1;
	}

	/* read back the level to confirm the write succeeded */
	printf("read back level to confirm write succeeded\n");
	ret = wasp_if_object_get_property_num(
		ipv4_address,
		level_ctrl_id,
		level_level_prop,
		strlen(level_level_prop),
		&val,
		0
	);
	if (ret == 200 || ret == 202) {
		printf("read level success\n");
	} else {
		printf("get level fail, err: %d\n", ret);
		return -1;
	}
	printf("current level: %ddBu\n", val);
	if (val != level_to_set) {
		printf("current level does not match what was sent\n");
		return -1;
	}

	printf("\n---level schemas---\n");

	/* read the schemas */
	ret = wasp_if_object_get_property_str(
		ipv4_address,
		level_ctrl_id,
		level_schema_prop,
		strlen(level_schema_prop),
		schema_id,
		sizeof(schema_id),
		1
	);
	if (ret == -1) {
		printf("error finding level object '_schema' property\n");
		return -1;
	}

	ret = wasp_if_schema_get_property_str(
		ipv4_address,
		schema_id,
		sizeof(schema_id),
		level_schema_units,
		strlen(level_schema_units),
		prop,
		sizeof(prop)
	);
	if (ret == -1) {
		printf("error finding level schema 'wasp-unit' property\n");
		return -1;
	}
	printf("level units: %s\n", prop);

	ret = wasp_if_schema_get_property_num(
		ipv4_address,
		schema_id,
		sizeof(schema_id),
		level_schema_min,
		strlen(level_schema_min),
		&val
	);
	if (ret == -1) {
		printf("error finding level schema 'minimum' property\n");
		return -1;
	}
	printf("minimum: %d\n", val);

	ret = wasp_if_schema_get_property_num(
		ipv4_address,
		schema_id,
		sizeof(schema_id),
		level_schema_max,
		strlen(level_schema_max),
		&val
	);
	if (ret == -1) {
		printf("error finding level schema 'maximum' property\n");
	}
	printf("maximum: %d\n", val);

	printf("\n---phantom power---\n");

	/* phantom power */
	phantom_ctrl_id = wasp_if_get_ctrl_id(
		ipv4_address,
		phantom_ctrl_type,
		strlen(phantom_ctrl_type),
		parent_id
	);
	if (phantom_ctrl_id == -1) {
		printf("error finding phantom power object ID\n");
		return -1;
	}

	/* read the phantom power state */
	ret = wasp_if_object_get_property_bool(
		ipv4_address,
		phantom_ctrl_id,
		phantom_active_prop,
		strlen(phantom_active_prop),
		&val,
		1
	);
	if (ret == -1) {
		printf("error finding phantom power object 'active' property ID\n");
		return -1;
	}
	printf("phantom power active: %s\n", (val ? "true" : "false"));

	/* write the phantom power state */
	printf("write phantom power state -> False\n");
	ret = wasp_if_object_set_property_bool(
		ipv4_address,
		phantom_ctrl_id,
		phantom_active_prop,
		strlen(phantom_active_prop),
		0
	);
	if (ret == 200 || ret == 202) {
		printf("set phantom power state success\n");
	} else {
		printf("set phantom power fail, err: %d\n", ret);
		return -1;
	}

	/* set the level and phantom in one request */
	printf("\n---set the level and phantom power state in one request---\n");
	snprintf(body,
		WASP_IF_BODY_LEN,
		"[{\"_id\":%d, \"active\":%s}, {\"_id\":%d, \"level\":%d}]",
		phantom_ctrl_id,
		"false",
		level_ctrl_id,
		24
	);
	ret = wasp_if_object_set_multiple_properties(
		ipv4_address,
		body,
		sizeof(body)
	);
	if (ret == 200 || ret == 202) {
		printf("set phantom power state and level success\n");
	} else {
		printf("set phantom power and level fail, err: %d\n", ret);
		return -1;
	}

	printf("\n---mute---\n");

	/* mute */
	mute_ctrl_id = wasp_if_get_ctrl_id(
		ipv4_address,
		mute_obj_type,
		strlen(mute_obj_type),
		parent_id
	);
	if (mute_ctrl_id == -1) {
		printf("error finding mute object ID\n");
		return -1;
	}

	/* read the mute state */
	ret = wasp_if_object_get_property_bool(
		ipv4_address,
		mute_ctrl_id,
		mute_active_prop,
		strlen(mute_active_prop),
		&val,
		1
	);
	if (ret == -1) {
		printf("error finding mute object 'active' property\n");
		return -1;
	}
	printf("mute active: %s\n", (val ? "true" : "false"));

	/* write the mute state */
	printf("write mute state ->  False\n");
	ret = wasp_if_object_set_property_bool(
		ipv4_address,
		mute_ctrl_id,
		mute_active_prop,
		strlen(mute_active_prop),
		0
	);
	if (ret == 200 || ret == 202) {
		printf("set mute state success\n");
	} else {
		printf("set mute fail, err: %d\n", ret);
		return -1;
	}

	return 0;
}

int device_identify(const char *ipv4_address)
{
	int ret = 0;
	int identify_id = 0;
	const char *device_identify_obj_type = "device:identify";
	const char *device_idetify_obj_identify_prop = "identify";

	printf("\n---Identify %s---\n", ipv4_address);

	/* find the identify object */
	identify_id = wasp_if_get_obj_id(
		ipv4_address,
		device_identify_obj_type,
		strlen(device_identify_obj_type),
		NULL,
		0,
		NULL,
		0,
		0
	);
	if (identify_id == -1) {
		printf("error finding device:identify object\n");
		return -1;
	}

	ret = wasp_if_object_set_property_bool(
		ipv4_address,
		identify_id,
		device_idetify_obj_identify_prop,
		strlen(device_idetify_obj_identify_prop),
		1
	);
	if (ret == 200 || ret == 202) {
		printf("identify ON\n");
	} else {
		printf("set identify fail, err: %d\n", ret);
		return -1;
	}

	/* wait */
	sleep(6);

	/* identify OFF */
	ret = wasp_if_object_set_property_bool(
		ipv4_address,
		identify_id,
		device_idetify_obj_identify_prop,
		strlen(device_idetify_obj_identify_prop),
		0
	);
	if (ret != 200 && ret != 202) {
		printf("set identify fail, err: %d\n", ret);
		return -1;
	}
	printf("identify OFF\n");

	return 0;
}

int device_info(const char *ipv4_address)
{
	int ret = 0;
	int user_data_id = 0;
	int hw_desc_id = 0;
	int sw_desc_id = 0;
	char prop[WASP_IF_OBJ_PROP_LEN];
	const char *device_user_data_obj_type = "device:user_data";
	const char *device_user_data_label_prop = "label";
	const char *device_user_data_label = "custom label";
	const char *device_hw_desc_obj_type = "device:hw_desc";
	const char *device_hw_obj_name_prop = "name";
	const char *device_sw_desc_obj_type = "device:sw_desc";
	const char *device_sw_obj_version_prop = "version";

	/* find the user data object */
	user_data_id = wasp_if_get_obj_id(
		ipv4_address,
		device_user_data_obj_type,
		strlen(device_user_data_obj_type),
		NULL,
		0,
		NULL,
		0,
		0
	);
	if (user_data_id == -1) {
		printf("error finding device:userdata object\n");
		return -1;
	}

	/* find the hardware descriptor object */
	hw_desc_id = wasp_if_get_obj_id(
		ipv4_address,
		device_hw_desc_obj_type,
		strlen(device_hw_desc_obj_type),
		NULL,
		0,
		NULL,
		0,
		0
	);
	if (hw_desc_id == -1) {
		printf("error finding device:hw_desc object\n");
		return -1;
	}

	/* find the software descriptor object */
	sw_desc_id = wasp_if_get_obj_id(
		ipv4_address,
		device_sw_desc_obj_type,
		strlen(device_sw_desc_obj_type),
		NULL,
		0,
		NULL,
		0,
		0
	);
	if (sw_desc_id == -1) {
		printf("error finding device:sw_desc object\n");
		return -1;
	}

	printf("\n---read the hardware name---\n");

	/* read the hardware name */
	ret = wasp_if_object_get_property_str(
		ipv4_address,
		hw_desc_id,
		device_hw_obj_name_prop,
		strlen(device_hw_obj_name_prop),
		prop,
		sizeof(prop),
		1
	);
	if (ret == -1) {
		printf("error finding hardware name\n");
		return -1;
	}
	printf("hardware name: %s\n", prop);

	printf("\n---read the software version---\n");

	/* read the software version */
	ret = wasp_if_object_get_property_str(
		ipv4_address,
		sw_desc_id,
		device_sw_obj_version_prop,
		strlen(device_sw_obj_version_prop),
		prop,
		sizeof(prop),
		1
	);
	if (ret == -1) {
		printf("error finding software version\n");
		return -1;
	}
	printf("software version: %s\n", prop);

	printf("\n---write a custom device label---\n");

	/* set a custom user data device label */
	ret = wasp_if_object_set_property_str(
		ipv4_address,
		user_data_id,
		device_user_data_label_prop,
		strlen(device_user_data_label_prop),
		device_user_data_label,
		strlen(device_user_data_label)
	);
	if (ret == 200 || ret == 202) {
		printf("set device info user data label success\n");
	} else {
		printf("set device info user data label fail, err: %d\n", ret);
		return -1;
	}

	return 0;
}

int gpi(const char *ipv4_address, int index)
{
	int ret = 0;
	int parent_id = 0;
	int gpi_ctrl_id = 0;
	char prop[WASP_IF_OBJ_PROP_LEN];
	const char *type = "block:io";
	const char *gpi_obj_type = "ctrl:gpi";
	const char *io_type = "gpio";
	const char *io_dir = "in";
	const char *gpi_obj_bits_state_prop = "bits_state";

	printf("\n---look up the GPI index %d IO block---\n", index);

	parent_id = wasp_if_get_obj_id(
		ipv4_address,
		type,
		strlen(type),
		io_type,
		strlen(io_type),
		io_dir,
		strlen(io_dir),
		index
	);
	if (parent_id == -1) {
		printf("cannot finding GPI IO block %d\n", index);
		return -1;
	}

	printf("\n---find the GPI child control of this block---\n");

	/* GPI */
	gpi_ctrl_id = wasp_if_get_ctrl_id(
		ipv4_address,
		gpi_obj_type,
		strlen(gpi_obj_type),
		parent_id
	);
	if (gpi_ctrl_id == -1) {
		printf("error finding GPI control ID\n");
		return -1;
	}

	/* bits state */
	printf("read GPI bits state\n");
	ret = wasp_if_object_get_property_str(
		ipv4_address,
		gpi_ctrl_id,
		gpi_obj_bits_state_prop,
		strlen(gpi_obj_bits_state_prop),
		prop,
		sizeof(prop),
		1
	);
	if (ret == -1) {
		printf("error finding GPI 'bits_state' property\n");
		return -1;
	}
	printf("bits_state: %s\n", prop);

	return 0;
}

int gpo(const char *ipv4_address, int index)
{
	int ret = 0;
	int parent_id = 0;
	int gpo_ctrl_id = 0;
	char prop[WASP_IF_OBJ_PROP_LEN];
	const char *type = "block:io";
	const char *gpo_obj_type = "ctrl:gpo";
	const char *io_type = "gpio";
	const char *io_dir = "out";
	const char *gpo_obj_bits_state_prop = "bits_state";
	const char *bits = "0001";

	printf("\n---look up the GPO index %d IO block---\n", index);

	parent_id = wasp_if_get_obj_id(
		ipv4_address,
		type,
		strlen(type),
		io_type,
		strlen(io_type),
		io_dir,
		strlen(io_dir),
		index
	);
	if (parent_id == -1) {
		printf("cannot find GPO IO block %d\n", index);
		return -1;
	}

	printf("\n---find the GPO child control of this block---\n");

	/* GPO */
	gpo_ctrl_id = wasp_if_get_ctrl_id(
		ipv4_address,
		gpo_obj_type,
		strlen(gpo_obj_type),
		parent_id
	);
	if (gpo_ctrl_id == -1) {
		printf("error finding GPO control ID\n");
		return -1;
	}

	/* bits state */
	printf("read GPO bits state\n");
	ret = wasp_if_object_get_property_str(
		ipv4_address,
		gpo_ctrl_id,
		gpo_obj_bits_state_prop,
		strlen(gpo_obj_bits_state_prop),
		prop,
		sizeof(prop),
		1
	);
	if (ret == -1) {
		printf("error finding GPO 'bits_state' property\n");
		return -1;
	}
	printf("bits_state: %s\n", prop);

	/* set a single bit */
	printf("write a single GPO bit\n");

	ret = wasp_if_object_set_property_str(
		ipv4_address,
		gpo_ctrl_id,
		gpo_obj_bits_state_prop,
		strlen(gpo_obj_bits_state_prop),
		bits,
		strlen(bits)
	);
	if (ret == 200 || ret == 202) {
		printf("set gpo bits success\n");
	} else {
		printf("set gpo 'bits_state' fail, err: %d\n", ret);
		return -1;
	}

	/* read back the bits to confirm they are set */
	printf("read the GPO bits to confirm they match what was set\n");
	ret = wasp_if_object_get_property_str(
		ipv4_address,
		gpo_ctrl_id,
		gpo_obj_bits_state_prop,
		strlen(gpo_obj_bits_state_prop),
		prop,
		sizeof(prop),
		0
	);
	if (ret == 200 || ret == 202) {
		printf("set gpo bits success\n");
	} else {
		printf("set gpo 'bits_state' fail, err: %d\n", ret);
		return -1;
	}
	printf("bits_state: %s\n", prop);

	if (strcmp(prop, bits)) {
		printf("bits state does not match what was sent\n");
		return -1;
	}

	return 0;
}

void object_update_stream_callback(const char *ipv4_address, const char *path, const char *update_body)
{
	(void)ipv4_address;
	(void)path;
	(void)update_body;

	/* uncomment the following line to read from object update stream */

	//printf("Object update, IP address - %s path - %s body - %s\n", ipv4_address, path, update_body);
}

int main()
{
	printf("\n***Starting example app***\n\n");

	const char *dev1 = "192.168.1.231";
	const char *dev2 = "192.168.1.193";

	/* start the WASP interface */
	if (wasp_if_init(object_update_stream_callback)) {
		printf("error starting WASP interface\n");
		return -1;
	}

	/* connect to 2 devices */
	wasp_if_connect_to_device(0, dev1, 1);
	wasp_if_connect_to_device(1, dev2, 1);

	/* identify the devices */
	device_identify(dev1);
	device_identify(dev2);

	/* read the stored device info */
	device_info(dev1);
	device_info(dev2);

	/* read stored analog input objects/schemas, test GET/SET */
	analog_input(dev1, 0);
	analog_input(dev2, 0);

	/* GPIO example */
	gpi(dev1, 0);
	gpi(dev2, 0);
	gpo(dev1, 0);
	gpo(dev2, 0);

	printf("\n***Exiting...***\n");

	return 0;
}

