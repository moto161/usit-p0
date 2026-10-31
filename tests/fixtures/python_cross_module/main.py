import alpha as mod
from beta import same as beta_same
from math import sqrt

def invoke():
    mod.same()
    beta_same()
    sqrt(4)
    missing()
    getattr(mod, "same")()

def dynamic(func):
    func()

def shadowed_module(mod):
    mod.same()
