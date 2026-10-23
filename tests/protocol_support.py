"""The same rejected external values are exercised independently at HTTP and Unix boundaries."""

INVALID_ADDRESSES = (
    '', '@rsalehin24.me', 'outside@example.net', 'two@@rsalehin24.me',
    '--help@rsalehin24.me', '.leading@rsalehin24.me', 'trailing.@rsalehin24.me',
    'two..segments@rsalehin24.me', 'space here@rsalehin24.me',
    'path/name@rsalehin24.me', 'path\\name@rsalehin24.me',
    'line\nfeed@rsalehin24.me', 'carriage\rreturn@rsalehin24.me', 'nul\0byte@rsalehin24.me',
    'x' * 65 + '@rsalehin24.me', 'postmaster@rsalehin24.me', '非ASCII@rsalehin24.me',
)
INVALID_PASSWORDS = ('', 'x' * 15, 'é' * 36 + ' ', 'x' * 73,
                     'x' * 16 + '\r', 'x' * 16 + '\n', 'x' * 16 + '\0')
