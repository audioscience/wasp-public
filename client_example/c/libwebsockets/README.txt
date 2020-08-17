This directory contains an example WASP application built upon an interface that uses:
	- mjson (https://github.com/cesanta/mjson), for JSON parsing
	- libwebsockets (https://github.com/warmcat/libwebsockets) for a HTTP client

Contents:
|
|- example_app.c (WASP example code)
|- wasp_interface.h/c (API for reading/writing WASP objects/schemas)
|- json.h/c (a wrapper for mjson)
|- lws_http_client.h/c (libwebsockets HTTP client)
|- cmakelists.txt (CMake file)

How to Run:
	- compile and install libwebsockets
	- cmake [build_directory]
	- in example_app.c main(), replace IPv4 address(es):
		---
		const char *dev1 = "192.168.1.231";
		...
		---
	- make
	- ./example_app

Example Code:

example_app connects to two WASP devices and demonstrates:
	1) identifying a device
	2) reading device info
	3) reading/writing analog input parameters (e.g. level, phantom power, mute)
	4) reading/writing GPIO parameters
	5) reading from the object update stream

Abstraction Layer:

wasp_interface is designed to be portable across any end user application.

Implementation Details:

The Libwebsockets HTTP client is run on an additional thread to allow
the application to continuously receive the object update stream while
sending and waiting for the response of other requests. Each GET/PATCH/POST
request is processed sequentially.

