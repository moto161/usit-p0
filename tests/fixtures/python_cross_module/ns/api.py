__all__ = ["public_api"]

def public_api():
    return 1

def hidden_api():
    return 2

def mark(fn):
    return fn

@mark
def decorated_api():
    return 3
