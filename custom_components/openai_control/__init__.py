"""The OpenAI Control integration."""
from __future__ import annotations

import json
import re

from functools import partial
import logging
from typing import Any, Literal

from string import Template

import openai
from openai import error

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, TemplateError
from homeassistant.helpers import intent, template, entity_registry
from homeassistant.util import ulid

from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    ENTITY_TEMPLATE,
    SYSTEM_PROMPT,
    PROMPT_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)

entity_template = Template(ENTITY_TEMPLATE)
prompt_template = Template(PROMPT_TEMPLATE)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OpenAI Agent from a config entry."""
    openai.api_key = entry.data[CONF_API_KEY]

    try:
        await hass.async_add_executor_job(
            partial(openai.Engine.list, request_timeout=10)
        )
    except error.AuthenticationError as err:
        _LOGGER.error("Invalid API key: %s", err)
        return False
    except error.OpenAIError as err:
        raise ConfigEntryNotReady(err) from err

    conversation.async_set_agent(hass, entry, OpenAIAgent(hass, entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload OpenAI Agent."""
    openai.api_key = None
    conversation.async_unset_agent(hass, entry)
    return True


def _entry_ext_dict(entry: er.RegistryEntry) -> dict[str, Any]:
    """Convert entry to API format."""
    data = entry.as_partial_dict
    data["aliases"] = entry.aliases
    data["capabilities"] = entry.capabilities
    data["device_class"] = entry.device_class
    data["original_device_class"] = entry.original_device_class
    data["original_icon"] = entry.original_icon
    return data

class OpenAIAgent(conversation.AbstractConversationAgent):
    """OpenAI Control Agent."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        self.history: dict[str, list[dict]] = {}

    @property
    def attribution(self):
        """Return the attribution."""
        return {"name": "Powered by OpenAI", "url": "https://www.openai.com"}

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:

        """ Options input """

        raw_prompt = self.entry.options.get(CONF_PROMPT, DEFAULT_PROMPT)
        model = self.entry.options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
        max_tokens = self.entry.options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
        top_p = self.entry.options.get(CONF_TOP_P, DEFAULT_TOP_P)
        temperature = self.entry.options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)

        """ Start a sentence """

        # check if the conversation is continuing or new

        # generate the prompt to be added to the sending messages later
        try:
            prompt = self._async_generate_prompt(raw_prompt)
        except TemplateError as err:

            _LOGGER.error("Error rendering prompt: %s", err)

            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Sorry, I had a problem with my template: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=conversation_id
            )

        # if continuing then get the messages from the conversation history
        if user_input.conversation_id in self.history:
            conversation_id = user_input.conversation_id
            messages = self.history[conversation_id]
        # if new then create a new conversation history
        else:
            conversation_id = ulid.ulid()
            # add the conversation starter to the begining of the conversation
            # this is to give the assistant more personality
            messages = [{"role": "system", "content": prompt + SYSTEM_PROMPT}]

        """ Entities """

        # Get all entities exposed to the Conversation Assistant

        registry = entity_registry.async_get(self.hass)
        entity_ids = self.hass.states.async_entity_ids()
        all_services = self.hass.services.async_services()


        entities_template = ''
        for entity_id in entity_ids:
            # get entities from the registry
            # to determine if they are exposed to the Conversation Assistant
            # registry entries have the propert "options['conversation']['should_expose']"
            entity = registry.entities.get(entity_id)

            if not entity or not entity.options.get('conversation', {}).get('should_expose', False):
                _LOGGER.warn('Entity with id [%s] was None or should_expose was False %s', str(entity_id), str(entity))
                continue

            # get the status string
            status_object = self.hass.states.get(entity_id)
            status_string = status_object.state

            services = all_services.get(entity.domain, {}).keys()


            # append the entitites tempalte
            entities_template += entity_template.substitute(
                id=entity_id,
                domain=entity.domain,
                name=entity.name or entity_id,
                status=status_string or "unknown",
                action=','.join(services),
            )

        # generate the prompt using the prompt_template
        prompt_render = prompt_template.substitute(
            entities=entities_template,
            prompt=user_input.text
        )

        openai_messages = messages + [{"role": "user", "content": prompt_render}]

        messages.append({"role": "user", "content": user_input.text})

        _LOGGER.debug("Prompt for %s: %s", model, openai_messages)


        """ OpenAI Call """

        # call OpenAI
        try:
            result = await openai.ChatCompletion.acreate(
                model=model,
                messages=openai_messages,
                max_tokens=max_tokens,
                top_p=top_p,
                temperature=temperature,
                user=conversation_id,
            )
        except error.OpenAIError as err:
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Sorry, I had a problem talking to OpenAI: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=conversation_id
            )

        content = result["choices"][0]["message"]["content"]

        # set a default reply
        # this will be changed if a better reply is found
        reply = content

        _LOGGER.debug("Response for %s: %s", model, content)

        json_response = None

        # all responses should come back as a JSON, since we requested such in the prompt_template
        try:
            json_response = json.loads(content)
        except json.JSONDecodeError as err:
            _LOGGER.error('Error on first parsing of JSON message from OpenAI Error: [ %s ] --- Content: %s', err, content)

        # if the response did not come back as a JSON
        # attempt to extract JSON from the response
        # this is because GPT will sometimes prefix the JSON with a sentence

        start_idx = content.find('{')
        end_idx = content.rfind('}') + 1

        if start_idx is not -1 and end_idx is not -1:
            json_string = content[start_idx:end_idx]
            try:
                json_response = json.loads(json_string)
            except json.JSONDecodeError as err:
                _LOGGER.error('Error on second parsing of JSON message from OpenAI [ %s ] --- Content: %s', err, content)
        else:
            _LOGGER.error('Error on second extraction of JSON message from OpenAI, %s', content)

        # only operate on JSON actions if JSON was extracted
        if json_response is not None:

            # call the needed services on the specific entities

            try:
                for entity in json_response["entities"]:
                    if self.hass.services.has_service(entity['domain'], entity['action']):
                        await self.hass.services.async_call(entity['domain'], entity['action'], {'entity_id': entity['id']})
                        _LOGGER.debug('ACTION: %s', entity['action'])
                        _LOGGER.debug('ID: %s', entity['id'])
                    # else:
                    #     real_entity = registry.entities.get(entity_id)
                    #     status_object = self.hass.states.get(real_entity)
                    #     status_string = status_object.state
            except KeyError as err:
                _LOGGER.warn('No entities detected for prompt %s', user_input.text)

            # resond with the "assistant" field of the json_response

            try:
                reply = json_response['assistant']
            except KeyError as err:
                _LOGGER.error('Error extracting assistant response %s', user_input.text)
                intent_response = intent.IntentResponse(language=user_input.language)
                intent_response.async_set_error(
                    intent.IntentResponseErrorCode.UNKNOWN,
                    f"Sorry, there was an error understanding OpenAI: {err}",
                )
                return conversation.ConversationResult(
                    response=intent_response, conversation_id=conversation_id
                )
            

        messages.append({"role": "assistant", "content": reply})
        self.history[conversation_id] = messages


        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(reply)
        return conversation.ConversationResult(
            response=intent_response, conversation_id=conversation_id
        )

    def _async_generate_prompt(self, raw_prompt: str) -> str:
        """Generate a prompt for the user."""
        return template.Template(raw_prompt, self.hass).async_render(
            {
                "ha_name": self.hass.config.location_name,
            },
            parse_result=False,
        )
