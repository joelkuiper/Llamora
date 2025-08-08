def str_to_bool(value: str) -> bool:
    truthy_values = {"true", "1", "yes", "y", "t", "on"}
    falsy_values = {"false", "0", "no", "n", "f", "off"}

    value = (
        value.strip().lower()
    )  # Normalize the input to lower case and strip extra spaces

    if value in truthy_values:
        return True
    elif value in falsy_values:
        return False
    else:
        raise ValueError(f"Cannot convert '{value}' to a boolean.")
