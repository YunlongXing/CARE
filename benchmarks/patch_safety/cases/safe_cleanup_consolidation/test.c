#include <stdlib.h>

int process(char *p, int fail);

int main(void) {
    char *first = malloc(1);
    char *second = malloc(1);
    if (process(NULL, 0) != -1)
        return 1;
    if (!first || process(first, 0) != 0)
        return 2;
    if (!second || process(second, 1) != -2)
        return 3;
    return 0;
}
