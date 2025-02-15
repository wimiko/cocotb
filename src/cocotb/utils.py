# Copyright (c) 2013 Potential Ventures Ltd
# Copyright (c) 2013 SolarFlare Communications Inc
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of Potential Ventures Ltd,
#       SolarFlare Communications Inc nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL POTENTIAL VENTURES LTD BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Collection of handy functions."""
import inspect
import math
import os
import sys
import traceback
import weakref
from decimal import Decimal
from numbers import Real
from typing import Union

from cocotb import simulator


def _get_simulator_precision():
    # cache and replace this function
    precision = simulator.get_precision()
    global _get_simulator_precision
    _get_simulator_precision = precision.__int__
    return _get_simulator_precision()


# Simulator helper functions
def get_sim_time(units: str = "step") -> int:
    """Retrieves the simulation time from the simulator.

    Args:
        units: String specifying the units of the result
            (one of ``'step'``, ``'fs'``, ``'ps'``, ``'ns'``, ``'us'``, ``'ms'``, ``'sec'``).
            ``'step'`` will return the raw simulation time.

            .. versionchanged:: 2.0
                Passing ``None`` as the *units* argument was removed, use ``'step'`` instead.

    Returns:
        The simulation time in the specified units.

    .. versionchanged:: 1.6.0
        Support ``'step'`` as the the *units* argument to mean "simulator time step".
    """
    timeh, timel = simulator.get_sim_time()

    result = timeh << 32 | timel

    if units != "step":
        result = get_time_from_sim_steps(result, units)

    return result


def _ldexp10(frac, exp):
    """Like math.ldexp, but base 10"""
    # using * or / separately prevents rounding errors if `frac` is a
    # high-precision type
    if exp > 0:
        return frac * (10**exp)
    else:
        return frac / (10**-exp)


def get_time_from_sim_steps(steps: int, units: str) -> int:
    """Calculates simulation time in the specified *units* from the *steps* based
    on the simulator precision.

    Args:
        steps: Number of simulation steps.
        units: String specifying the units of the result
            (one of ``'fs'``, ``'ps'``, ``'ns'``, ``'us'``, ``'ms'``, ``'sec'``).

    Returns:
        The simulation time in the specified units.
    """
    return _ldexp10(steps, _get_simulator_precision() - _get_log_time_scale(units))


def get_sim_steps(
    time: Union[Real, Decimal], units: str = "step", *, round_mode: str = "error"
) -> int:
    """Calculates the number of simulation time steps for a given amount of *time*.

    When *round_mode* is ``"error"``, a :exc:`ValueError` is thrown if the value cannot
    be accurately represented in terms of simulator time steps.
    When *round_mode* is ``"round"``, ``"ceil"``, or ``"floor"``, the corresponding
    rounding function from the standard library will be used to round to a simulator
    time step.

    Args:
        time: The value to convert to simulation time steps.
        units: String specifying the units of the result
            (one of ``'step'``, ``'fs'``, ``'ps'``, ``'ns'``, ``'us'``, ``'ms'``, ``'sec'``).
            ``'step'`` means *time* is already in simulation time steps.
        round_mode: String specifying how to handle time values that sit between time steps
            (one of ``'error'``, ``'round'``, ``'ceil'``, ``'floor'``).

    Returns:
        The number of simulation time steps.

    Raises:
        ValueError: if the value cannot be represented accurately in terms of simulator
            time steps when *round_mode* is ``"error"``.

    .. versionchanged:: 1.5
        Support ``'step'`` as the *units* argument to mean "simulator time step".

    .. versionchanged:: 1.6
        Support rounding modes.
    """
    if units != "step":
        result = _ldexp10(time, _get_log_time_scale(units) - _get_simulator_precision())
    else:
        result = time

    if round_mode == "error":
        result_rounded = math.floor(result)
        if result_rounded != result:
            precision = _get_simulator_precision()
            raise ValueError(
                f"Unable to accurately represent {time}({units}) with the simulator precision of 1e{precision}"
            )
    elif round_mode == "ceil":
        result_rounded = math.ceil(result)
    elif round_mode == "round":
        result_rounded = round(result)
    elif round_mode == "floor":
        result_rounded = math.floor(result)
    else:
        raise ValueError(f"Invalid round_mode specifier: {round_mode}")

    return result_rounded


def _get_log_time_scale(units):
    """Retrieves the ``log10()`` of the scale factor for a given time unit.

    Args:
        units (str): String specifying the units
            (one of ``'fs'``, ``'ps'``, ``'ns'``, ``'us'``, ``'ms'``, ``'sec'``).

    Returns:
        The ``log10()`` of the scale factor for the time unit.
    """
    scale = {"fs": -15, "ps": -12, "ns": -9, "us": -6, "ms": -3, "sec": 0}

    units_lwr = units.lower()
    if units_lwr not in scale:
        raise ValueError(f"Invalid unit ({units}) provided")
    else:
        return scale[units_lwr]


class ParametrizedSingleton(type):
    """A metaclass that allows class construction to reuse an existing instance.

    We use this so that :class:`RisingEdge(sig) <cocotb.triggers.RisingEdge>` and :class:`Join(coroutine) <cocotb.triggers.Join>` always return
    the same instance, rather than creating new copies.
    """

    def __init__(cls, *args, **kwargs):
        # Attach a lookup table to this class.
        # Weak such that if the instance is no longer referenced, it can be
        # collected.
        cls.__instances = weakref.WeakValueDictionary()

    def __singleton_key__(cls, *args, **kwargs):
        """Convert the construction arguments into a normalized representation that
        uniquely identifies this singleton.
        """
        # Could default to something like this, but it would be slow
        # return tuple(inspect.Signature(cls).bind(*args, **kwargs).arguments.items())
        raise NotImplementedError

    def __call__(cls, *args, **kwargs):
        key = cls.__singleton_key__(*args, **kwargs)
        try:
            return cls.__instances[key]
        except KeyError:
            # construct the object as normal
            self = super().__call__(*args, **kwargs)
            cls.__instances[key] = self
            return self

    @property
    def __signature__(cls):
        return inspect.signature(cls.__singleton_key__)


def want_color_output():
    """Return ``True`` if colored output is possible/requested and not running in GUI.

    Colored output can be explicitly requested by setting :envvar:`COCOTB_ANSI_OUTPUT` to  ``1``.
    """
    want_color = sys.stdout.isatty()  # default to color for TTYs
    if os.getenv("NO_COLOR") is not None:
        want_color = False
    if os.getenv("COCOTB_ANSI_OUTPUT", default="0") == "1":
        want_color = True
    if os.getenv("GUI", default="0") == "1":
        want_color = False
    return want_color


def remove_traceback_frames(tb_or_exc, frame_names):
    """
    Strip leading frames from a traceback

    Args:
        tb_or_exc (Union[traceback, BaseException, exc_info]):
            Object to strip frames from. If an exception is passed, creates
            a copy of the exception with a new shorter traceback. If a tuple
            from `sys.exc_info` is passed, returns the same tuple with the
            traceback shortened
        frame_names (List[str]):
            Names of the frames to strip, which must be present.
    """
    # self-invoking overloads
    if isinstance(tb_or_exc, BaseException):
        exc = tb_or_exc
        return exc.with_traceback(
            remove_traceback_frames(exc.__traceback__, frame_names)
        )
    elif isinstance(tb_or_exc, tuple):
        exc_type, exc_value, exc_tb = tb_or_exc
        exc_tb = remove_traceback_frames(exc_tb, frame_names)
        return exc_type, exc_value, exc_tb
    # base case
    else:
        tb = tb_or_exc
        for frame_name in frame_names:
            assert tb.tb_frame.f_code.co_name == frame_name
            tb = tb.tb_next
        return tb


def walk_coro_stack(coro):
    """Walk down the coroutine stack, starting at *coro*."""
    while coro is not None:
        try:
            f = getattr(coro, "cr_frame")
            coro = coro.cr_await
        except AttributeError:
            f = None
            coro = None
        if f is not None:
            yield (f, f.f_lineno)


def extract_coro_stack(coro, limit=None):
    """Create a list of pre-processed entries from the coroutine stack.

    This is based on :func:`traceback.extract_tb`.

    If *limit* is omitted or ``None``, all entries are extracted.
    The list is a :class:`traceback.StackSummary` object, and
    each entry in the list is a :class:`traceback.FrameSummary` object
    containing attributes ``filename``, ``lineno``, ``name``, and ``line``
    representing the information that is usually printed for a stack
    trace.  The line is a string with leading and trailing
    whitespace stripped; if the source is not available it is ``None``.
    """
    return traceback.StackSummary.extract(walk_coro_stack(coro), limit=limit)
