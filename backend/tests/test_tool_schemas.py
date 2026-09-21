"""Provider-compatible advertising must not weaken local argument validation."""
import pytest

from app.agent.registry import ToolRegistry
from app.agent.tools.base import Tool
from app.agent.tools.public_api import QueryPublicApi
from app.agent.validation import argument_error
from app.providers.bedrock_provider import BedrockProvider
from app.providers.openai_provider import OpenAIProvider


def test_public_api_wire_schemas_omit_top_level_composition_and_preserve_constraints():
    tool = QueryPublicApi()
    registry = ToolRegistry()
    registry.register(tool)
    specs = registry.specs()
    schemas = [specs[0].parameters,
               OpenAIProvider._tools_to_wire(specs)[0]['function']['parameters'],
               BedrockProvider._tools_to_wire(specs)['tools'][0]['toolSpec']['inputSchema']['json']]
    for schema in schemas:
        assert schema['type'] == 'object'
        assert not {'oneOf', 'allOf', 'anyOf'} & schema.keys()
        assert schema['additionalProperties'] is False
        assert schema['properties'] == tool.parameters['properties']
        assert schema['properties'] is not tool.parameters['properties']
    assert 'oneOf' in tool.parameters  # Still used by dispatch and direct invocation.


@pytest.mark.parametrize('arguments', [
    {}, {'adapter': 'pubmed'}, {'query': 'asthma'}, {'org_names': ['Example']},
    {'adapter': 'pubmed', 'org_names': ['Example'], 'fiscal_years': [2024]},
    {'adapter': 'nih_projects', 'query': 'asthma'},
    {'adapter': 'pubmed', 'query': 'asthma', 'org_names': ['Example']},
    {'continue_from': 'S1', 'query': 'asthma'}, {'continue_from': 'S1', 'limit': 2},
    {'adapter': 'pubmed', 'query': 'asthma', 'url': 'https://example.org'},
])
def test_invalid_unions_still_fail_before_approval_or_direct_execution(arguments):
    tool = QueryPublicApi()
    assert argument_error(tool, arguments)
    assert tool.run(None, **arguments).is_error  # Does not touch context/database/network.


@pytest.mark.parametrize('arguments', [
    {'org_names': ['Example'], 'fiscal_years': [2024]},
    {'adapter': 'nih_projects', 'org_names': ['Example'], 'fiscal_years': [2024], 'limit': 2},
    {'adapter': 'pubmed', 'query': 'asthma[Title]', 'limit': 2},
    {'continue_from': 'S1'}, {'adapter': 'pubmed', 'continue_from': 'S1'},
])
def test_supported_argument_modes_remain_valid(arguments):
    assert argument_error(QueryPublicApi(), arguments) is None


def test_tools_without_advertising_override_keep_existing_schema():
    tool = Tool()
    tool.name = 'plain'
    registry = ToolRegistry()
    registry.register(tool)
    assert registry.specs()[0].parameters is tool.parameters
