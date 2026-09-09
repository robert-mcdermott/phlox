"""Validate tool contracts locally, without fetching remote schema references."""
from jsonschema import Draft202012Validator
from referencing import Registry


def argument_error(tool, arguments):
    if not isinstance(arguments, dict):
        return f'Invalid arguments for {tool.name}: provide a JSON object matching its schema.'
    try:
        # An explicit empty registry disables jsonschema's default remote-ref retrieval.
        validator = Draft202012Validator(tool.parameters, registry=Registry())
        invalid = next(validator.iter_errors(arguments), None)
        if invalid is not None:
            return (f'Invalid arguments for {tool.name}: {invalid.validator} validation failed at '
                    f"{'.'.join(map(str, invalid.path)) or 'arguments'}. Correct the call to match its schema.")
    except Exception:
        return f'Tool schema for {tool.name} could not be validated locally. Not executed.'
    return None
