from typing import Any
from IPython.display import clear_output, display, HTML
import html
import re
import copy
import json
import textwrap
import numpy as np
from .._grammar import StatelessFunction, StatefulFunction, tag_start, tag_end, _string, _call_pool

class Endpoint:
    '''This keeps state that is shared among all models using the same endpoint session.'''
    pass

_null_grammar = _string('')

class Model:
    _open_contexts = {}

    def __init__(self, echo=True):

        # while models are logically immutable, they can share some mutable caching state to save computation
        self._cache_state  = {}
        self.echo = echo
        self._state = ""
        self._children = []
        self._opened_contexts = {}
        self._event_queue = None
        self._event_parent = None
        self._silent = None
        self._variables = {}
        self.instance__enter__ = []
        self.instance__exit__ = []
        self._streaming = False

        self._tag_pattern = re.compile(re.escape(tag_start) + r"([^\|]+)" + re.escape(tag_end))

    def __call__(self, pattern=None, max_tokens=100, n=1, top_p=1, temperature=0.0, ensure_bos_token=True):
        pass # meant to be overriden by subclasses

    def get(self, key, default=None):
        return self._variables.get(key, default)

    def set(self, key, value):
        copy = self.copy()
        copy._variables[key] = value
        return copy
    
    def remove(self, key):
        if key in self._variables:
            del self._variables[key]

    def _html(self):
        display_out = self._state
        for context in reversed(self._opened_contexts):
            display_out += self._opened_contexts[context]
        display_out = html.escape(display_out)
        display_out = re.sub(r"&lt;\|\|_#NODISP_\|\|&gt;.*?&lt;\|\|_/NODISP_\|\|&gt;", "", display_out, flags=re.DOTALL)
        display_out = re.sub(r"&lt;\|\|_html:(.*?)_\|\|&gt;", lambda x: html.unescape(x.group(1)), display_out, flags=re.DOTALL)
        display_out = "<pre style='margin: 0px; padding: 0px; padding-left: 8px; margin-left: -8px; border-radius: 0px; border-left: 1px solid rgba(127, 127, 127, 0.2); white-space: pre-wrap; font-family: ColfaxAI, Arial; font-size: 15px; line-height: 23px;'>"+display_out+"</pre>"
        return display_out
    
    def _send_to_event_queue(self, value):
        if self._event_queue is not None:
            self._event_queue.put(value)
        if self._event_parent is not None:
            self._event_parent._send_to_event_queue(value)

    def is_silent(self):
        if self._silent is not None:
            return self._silent
        return False
    
    def copy(self):
        new_lm = copy.copy(self)
        new_lm._event_queue = None
        if self._event_queue is not None:
            new_lm._event_parent = self
        new_lm._variables = self._variables.copy()
        new_lm._children = []
        self._children.append(new_lm)
        return new_lm
    
    def _inplace_append(self, value, force_silent=False):
        """This is the base way to add content to the LM object."""
        self._state += str(value)
        if self.echo and not self.is_silent() and not force_silent:
            clear_output(wait=True)
            display(HTML(self._html()))
        self._send_to_event_queue(self)
        return self
    
    def reset(self, clear_variables=True):
        """This resets the state of the LM prompt."""
        self._reset(0, clear_variables)
        return self

    def _reset(self, position=0, clear_variables=True):
        self._state = self._state[:position]
        if clear_variables:
            self._variables = {}

    def _repr_html_(self):
        clear_output(wait=True)
        return self._html()
    
    def __str__(self) -> str:
        return re.sub(r"<\|\|_.*?_\|\|>", "", self._state)
    
    def __add__(self, value):

        # close any newly closed contexts
        for context in list(reversed(self._opened_contexts)):
            if context not in Model._open_contexts and context in self._opened_contexts:
                close_text = self._opened_contexts[context] # save so we can delete it before adding it
                del self._opened_contexts[context]
                self._inplace_append(close_text)

        # apply any newly opened contexts (newly from the our perspective)
        for context in Model._open_contexts:
            if context not in self._opened_contexts:
                self._opened_contexts[context] = "" # mark this so we don't readd when computing the opener (even though we don't know the close text yet)
                self += context.opener
                tmp = self + context.closer
                close_text = tmp._state[len(self._state):] # get the new state added by calling the closer
                self._opened_contexts[context] = close_text
        
        # wrap raw string values
        if isinstance(value, str):
            is_id = False
            lm = self.copy()
            parts = re.split(self._tag_pattern, value)
            
            # we have no embedded objects
            if len(parts) == 1:
                return lm._inplace_append(value)
            
            # if we have embedded objects we have to convert the string to a grammar tree
            partial_grammar = _null_grammar
            lm.suffix = ""
            for i,part in enumerate(parts):
                if i < len(parts) - 1:
                    lm.suffix = parts[i+1]
                if is_id:
                    partial_grammar += _call_pool[part]
                elif part != "":
                    partial_grammar += _string(part)
                is_id = not is_id
            return lm + partial_grammar
        
        # run stateless functions (grammar nodes)
        elif isinstance(value, StatelessFunction):
            return self.copy().run_stateless(value)
        
        # run stateful functions
        else:
            return value(self)
    
    def endswith(self, s):
        return self._state.endswith(s)
    
    def __len__(self):
        return len(str(self))
    
    def __setitem__(self, key, value):
        self._variables[key] = value

    def __getitem__(self, key):
        return self._variables[key]
    
    def __contains__(self, item):
        return item in self._variables

    # def __enter__(self):
    #     Model._open_contexts
    #     self._opened_contexts
    #     if len(self.instance__enter__) > 0:
    #         return self.instance__enter__.pop()()

    # def __exit__(self, exc_type, exc_value, traceback):
    #     if len(self.instance__exit__) > 0:
    #         return self.instance__exit__.pop()(exc_type, exc_value, traceback)
    
    def get_cache(self):
        return self.engine.cache
    
    def tool_def(self, functions):

        self += """
# Tools

"""
        if len(functions) > 0:
            self += '''## functions

namespace functions {

'''
        for function in functions:
            self += f"""// {function['description']}
type {function['name']} = (_: {{"""
            for prop_name,prop_data in function["parameters"]["properties"].items():
                if "description" in prop_data:
                    self += f"\n// {prop_data['description']}\n"
                self += prop_name
                if prop_name not in function["parameters"]["required"]:
                    self += "?"
                self += ": "
                if "enum" in prop_data:
                    for enum in prop_data["enum"]:
                        self += f'"{enum}"'
                        if enum != prop_data["enum"][-1]:
                            self += " | "
                else:
                    self += prop_data["type"]
                
                if prop_name != list(function["parameters"]["properties"].keys())[-1]:
                    self += ",\n"
            self += """
}) => any;

"""
            self[function['name']] = function
        self += "} // namespace functions\n"
        
        return self

    def run_stateless(lm, stateless_function, max_tokens=1000, temperature=0.0, top_p=1.0, n=1):

        # This needs to be here for streaming
        # if name is not None:
        #     lm[name] = ""

        # start the generation stream
        gen_obj = lm(
            grammar=stateless_function, max_tokens=max_tokens, n=n,
            temperature=temperature, top_p=top_p
        )

        # single generation
        if n == 1:
            generated_value = ""
            # logprobs_out = []

            delayed_bytes = b""
            # last_is_generated = False
            for new_bytes,is_generated,new_bytes_log_prob,capture_groups,capture_group_log_probs in gen_obj:
                
                # convert the bytes to a string (delaying if we don't yet have a valid unicode string)
                new_bytes = delayed_bytes + new_bytes
                try:
                    new_text = new_bytes.decode("utf8")
                except UnicodeDecodeError:
                    delayed_bytes = new_bytes
                    continue
                delayed_bytes = b""

                generated_value += new_text
                if is_generated and len(new_bytes) > 0:
                    lm += f"<||_html:<span style='background-color: rgba(0, 165, 0, {0.15 + 0.4 * (1 - np.exp(new_bytes_log_prob))}); border-radius: 3px;' title='{new_bytes_log_prob}'>_||>"
                # if not is_generated and last_is_generated:
                    
                lm += new_text
                if is_generated and len(new_bytes) > 0:
                    lm += "<||_html:</span>_||>"
                
                # last_is_generated = is_generated

                if len(capture_groups) > 0:
                    for k in capture_groups:
                        v = capture_groups[k]
                        try:
                            lm[k] = v.decode("utf8") if isinstance(v, bytes) else v
                        except UnicodeDecodeError:
                            lm[k] = v

            # if len(capture_groups) > 0:
            #     for k in capture_groups:
            #         v = capture_groups[k]
            #         lm[k] = v.decode("utf8") if isinstance(v, bytes) else v
        
        return lm

class Chat(Model):
    
    def get_role_start(self, role_name, **kwargs):
        return "<|im_start|>"+role_name+"".join([f' {k}="{v}"' for k,v in kwargs.items()])+"\n"
    
    def get_role_end(self, role_name=None):
        return "<|im_end|>"