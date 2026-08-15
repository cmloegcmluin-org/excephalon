"""Every global name a function reaches for is actually there.

The gap this closes: `_session` in `__main__` used `head_commit`, which was imported inside a
DIFFERENT function. The whole suite passed - nothing drives `_session`, which needs a mic, a
model and a window - and the app died on the launch after the merge with `NameError: name
'head_commit' is not defined`, showing him a startup failure instead of a session.

A name is checked, never called: this walks the compiled bytecode for the globals each function
loads and asks whether the module (or builtins) actually defines them. Cheap, total, and it
cannot be fooled by a code path no test can reach.
"""

import builtins
import dis
import importlib
import pkgutil
import types

import pytest

import excephalon

MODULES = sorted(module.name for module in pkgutil.iter_modules(excephalon.__path__))


def _functions(module, seen):
    """Every function this module DEFINES, its classes' methods included. What it merely imported
    belongs to the module that wrote it, and is checked when that module's turn comes."""
    here = module.__name__
    written_in = getattr(module, "__file__", None)

    def mine(value):
        # Compiled from this module's own FILE: a dataclass's generated __repr__ claims the
        # module as its own while its code was built by the dataclasses machinery, and the names
        # that code reaches for are that machinery's, not this file's.
        return (getattr(value, "__module__", None) == here and value not in seen
                and value.__code__.co_filename == written_in)

    for value in vars(module).values():
        if isinstance(value, types.FunctionType) and mine(value):
            seen.add(value)
            yield value
        elif isinstance(value, type) and getattr(value, "__module__", None) == here:
            for member in vars(value).values():
                target = getattr(member, "__func__", member)
                if isinstance(target, types.FunctionType) and mine(target):
                    seen.add(target)
                    yield target


def _globals_loaded(code):
    """The global names this code object - and every code object inside it - loads."""
    for instruction in dis.get_instructions(code):
        if instruction.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
            yield instruction.argval
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            yield from _globals_loaded(constant)


@pytest.mark.parametrize("name", MODULES)
def test_every_global_a_function_reaches_for_is_defined(name):
    if name in ("google_bridge", "voiceprint"):
        return  # standalone entry points; importing them is their own tests' business
    module = importlib.import_module(f"excephalon.{name}")
    seen = set()
    missing = {}
    for function in _functions(module, seen):
        for wanted in _globals_loaded(function.__code__):
            if wanted in vars(module) or hasattr(builtins, wanted):
                continue
            # A name bound inside the function (a local import, an assignment) is a local, not a
            # global - so anything still here is genuinely absent when that line runs.
            if wanted in function.__code__.co_varnames:
                continue
            missing.setdefault(function.__qualname__, set()).add(wanted)
    assert not missing, f"excephalon.{name} reaches for names it does not have: {missing}"
