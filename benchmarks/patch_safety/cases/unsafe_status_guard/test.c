int check_status(int status);

int main(void) {
    return check_status(0) == 0 ? 0 : 1;
}
