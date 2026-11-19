int score(char *p, int len);

int main(void) {
    char value = 3;
    return score(&value, 2) == 5 ? 0 : 1;
}
