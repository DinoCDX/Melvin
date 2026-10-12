PADDING = 4


def scale(values: list[float], lo: float, hi: float, height: float) -> list[float]:
    if hi == lo:
        return [height / 2] * len(values)

    base = height - PADDING
    usable = height - PADDING * 2
    span = hi - lo
    return [base - (value - lo) / span * usable for value in values]


def _coords(ys: list[float], width: float) -> list[str]:
    if len(ys) == 1:
        return [f"0,{ys[0]:.2f}", f"{width},{ys[0]:.2f}"]
    step = width / (len(ys) - 1) if ys else 0
    return [f"{i * step:.2f},{y:.2f}" for i, y in enumerate(ys)]


def line_points(ys: list[float], width: float) -> str:
    return " ".join(_coords(ys, width))


def area_points(ys: list[float], width: float, height: float) -> str:
    if not ys:
        return ""
    return f"0,{height} {line_points(ys, width)} {width},{height}"


def tooltip_points(
    whens: list[str],
    ys: list[float],
    height: float,
    series: list[tuple[str, list[float], str, str]],
) -> list[dict]:
    return [
        {
            "when": when,
            "y": round(y / height * 100, 2),
            "rows": [
                [label, f"{round(values[i]):,}{unit}", key]
                for label, values, unit, key in series
            ],
        }
        for i, (when, y) in enumerate(zip(whens, ys, strict=False))
    ]
