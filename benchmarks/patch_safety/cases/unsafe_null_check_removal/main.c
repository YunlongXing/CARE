int score(char *p, int len) {
    if (!p)
        return -1;
    if (len <= 0)
        return -2;
    return p[0] + len;
}
