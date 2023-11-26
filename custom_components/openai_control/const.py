"""Constants for the OpenAI Control integration."""

DOMAIN = "openai_control"

ENTITY_TEMPLATE = """$id<>$name<>$domain<>$status<>$action
"""

SYSTEM_PROMPT = """
On every message you will be provided with a prompt and a list of devices, containing the device id, name, domain, state, and actions that can be performed.
The sections of the string are delimited by the string "<>"

JSON Template: { "entities": [ { "id": "", "domain": "", "action": "" } ], "assistant": "" }

Determine if the provided prompt is a command related to the provided entities. If the prompt is a command, respond only in JSON.
If the prompt is a question, do not execute a command, simply answer the question.

If the prompt is a command then:
    - Determine which entities relate to the above prompt and which action should be taken on those entities.
    - Respond only in the format of the above JSON Template.
    - Fill in the "assistant" field as a natural language responds for the action being taken.
    - Respond only with the JSON Template.

If it is not a command, answer the user's questions about the world truthfully.
"""

PROMPT_TEMPLATE = """Entities:
$entities

Prompt: "$prompt"
"""

"""Options"""

CONF_PROMPT = "prompt"
DEFAULT_PROMPT = """This smart home is controlled by Home Assistant."""

CONF_CHAT_MODEL = "chat_model"
DEFAULT_CHAT_MODEL = "gpt-3.5-turbo"

CONF_MAX_TOKENS = "max_tokens"
DEFAULT_MAX_TOKENS = 250

CONF_TOP_P = "top_p"
DEFAULT_TOP_P = 1

CONF_TEMPERATURE = "temperature"
DEFAULT_TEMPERATURE = 0.5
