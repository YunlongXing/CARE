#include <stdio.h>

int copy_name(char *dst, unsigned long size, const char *src) {
    if (!dst || !src || size == 0)
        return -1;
    snprintf(dst, size, "%s", src);
    return 0;
}
