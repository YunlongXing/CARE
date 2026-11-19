int classify(char *p, int len);

int main(void) {
    char value = 3;
    return classify(&value, 2) == 5 ? 0 : 1;
}
