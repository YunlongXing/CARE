int score(char *p, int len);

int main(void) {
    char value = 3;
    if (score(0, 1) != -1)
        return 1;
    if (score(&value, 0) != -2)
        return 2;
    return score(&value, 2) == 5 ? 0 : 3;
}
