"""Run interactively; never pass your password through chat or a shell argument."""
from getpass import getpass
from auth import password_hash

if __name__ == '__main__':
    password = getpass('Choose a Budget LPC password (at least 14 characters): ')
    if len(password) < 14:
        raise SystemExit('Use at least 14 characters.')
    if password != getpass('Repeat your password: '):
        raise SystemExit('Passwords do not match.')
    print('\nSet the hosting secret BUDGET_LPC_PASSWORD_HASH to this value:\n')
    print(password_hash(password))

