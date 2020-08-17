
/**********************************************
(C) Copyright AudioScience Inc. 2020
***********************************************/

#ifndef _JSON_H
#define _JSON_H

#include <stdlib.h>

int json_find(const char *s, int len, const char *path,
                          const char **tokptr, int *toklen);


int json_get_number(const char *s, int len, const char *path, double *v);

int json_get_bool(const char *s, int len, const char *path, int *v);

int json_get_string(const char *s, int len, const char *path, char *to, int sz);

int json_next(const char *s, int n, int off, int *koff, int *klen, int *voff,
               int *vlen, int *vtype);

#endif /*_JSON_H */
