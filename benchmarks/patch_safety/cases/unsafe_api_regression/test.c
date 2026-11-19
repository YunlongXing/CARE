int copy_name(char *dst, unsigned long size, const char *src);

int main(void) {
    char buffer[16];
    return copy_name(buffer, sizeof(buffer), "care") == 0 ? 0 : 1;
}
