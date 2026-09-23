"""Internal Engine handlers, called only after active-session ownership checks.

None means unhandled; [] means handled without replies and MUST stop dispatch.
Phase/state policies remain in the individual handlers. This is not a second
protocol registry or a public bypass around Engine.handle.
"""


def dispatch(handlers, engine, connection, message):
    for handler in handlers:
        result = handler(engine, connection, message)
        if result is not None:
            return result
    return None
