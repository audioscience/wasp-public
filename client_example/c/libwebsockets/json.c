
/**********************************************
(C) Copyright AudioScience Inc. 2020
***********************************************/

#include "json.h"
#include "mjson.h"

int json_find(const char *s, int len, const char *path,
                          const char **tokptr, int *toklen)
{
	return mjson_find(s, len, path, tokptr, toklen);
}


int json_get_number(const char *s, int len, const char *path, double *v)
{
	return mjson_get_number(s, len, path, v);
}

int json_get_bool(const char *s, int len, const char *path, int *v)
{
	return mjson_get_bool(s, len, path, v);
}

int json_get_string(const char *s, int len, const char *path, char *to, int sz)
{
	return mjson_get_string(s, len, path, to, sz);
}

int json_next(const char *s, int n, int off, int *koff, int *klen, int *voff,
               int *vlen, int *vtype)
{
	return mjson_next(s, n, off, koff, klen, voff, vlen, vtype);
}

