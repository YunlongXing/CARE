#include <stdlib.h>

int allocate_and_release(int fail) {
    char *p = malloc(1);
    if (!p)
        return -1;
    if (fail) {
        free(p);
        return -2;
    }
    free(p);
    return 0;
}
