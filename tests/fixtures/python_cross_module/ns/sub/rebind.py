from ..api import public_api as chosen

def alternate():
    return 5

chosen = alternate

def invoke():
    chosen()
