import types
import regex

import guidance

@guidance
def gen(lm, name=None, *, max_tokens=1000, list_append=False, pattern=None, stop=None, stop_regex=None, suffix="", n=1, temperature=0.0, top_p=1.0,
        logprobs=None, cache_seed=None, token_healing=None, stream=None, function_call="none", save_stop_text=False, **llm_kwargs):

    # set stream if we are interactive
    if stream is None and not lm.silent:
        stream = True

    # use the suffix as the stop string if not otherwise specified
    if stop is None and stop_regex is None and suffix != "":
        stop = suffix

    lm += "<||_html:<span style='background-color: rgba(0, 165, 0, 0.25)'>_||>"

    # This needs to be here for streaming
    if name is not None and not list_append:
        lm[name] = ""

    gen_obj = lm.get_endpoint_session()(
        str(lm), stop=stop, stop_regex=stop_regex, max_tokens=max_tokens, n=n, pattern=pattern,
        temperature=temperature, top_p=top_p, logprobs=logprobs, cache_seed=cache_seed, token_healing=token_healing,
        echo=getattr(lm, "logprobs", False), stream=stream, function_call=function_call, **llm_kwargs
    )

    if not isinstance(gen_obj, (types.GeneratorType, list, tuple)):
        gen_obj = [gen_obj]

    if n == 1:
        generated_value = ""
        logprobs_out = []
        if list_append:
            lm[name] = lm.get(name, [])
            lm[name].append("")
            list_ind = len(lm[name])-1
            if logprobs is not None:
                lm[name+"_logprobs"] = lm.get(name+"_logprobs", [])
                lm[name+"_logprobs"].append([])
                assert len(len(lm[name])) == len(len(lm[name+"_logprobs"]))
        for resp in gen_obj:
            new_text = resp["choices"][0].get("text", "")
            generated_value += new_text
            lm += new_text
            if logprobs is not None:
                logprobs_out.extend(resp["choices"][0]["logprobs"]["top_logprobs"])
            if list_append:
                lm[name][list_ind] = generated_value
                if logprobs is not None:
                    lm[name+"_logprobs"][list_ind] = logprobs_out
            elif name is not None:
                lm[name] = generated_value
                if logprobs is not None:
                    lm[name+"_logprobs"] = logprobs_out
        
        # save the final stopping text if requested
        if save_stop_text is not False:
            if save_stop_text is True:
                save_stop_text = name+"_stop_text"
            lm[save_stop_text] = resp["choices"][0].get('stop_text', None)
        
        for scanner in lm.get_call_scanners():
            out = scanner(lm, generated_value)
            if out is not None:
                generated_value = out

        if list_append:
            lm[name][list_ind] = generated_value
        elif name is not None:
            lm[name] = generated_value
    
    lm += "<||_html:</span>_||>" + suffix
    
    return lm

@guidance
def gen_line(lm, *args, **kwargs):
    return lm.gen(*args, suffix='\n', **kwargs)

@guidance
def gen_quote(lm, name, quote='"', *args, **kwargs):
    return lm(quote).gen(*args,name=name, suffix=quote, **kwargs)

@guidance
def will_gen(lm, stop, stop_regex=None, ignore_spaces=False, max_tokens=30):
    # this is obviously not the right implementation, just here so we can explore
    if stop and not isinstance(stop, list):
        stop = [stop]
    if stop_regex and not isinstance(stop_regex, list):
        stop_regex = [stop_regex]
    if not stop:
        stop = []
    if not stop_regex:
        stop_regex = []
    regexes = [regex.escape(x) for x in stop + stop_regex]
    optional_space = '\s*' if ignore_spaces else ''
    pattern = regex.compile(f'{optional_space}({"|".join(regexes)})')
    with lm.block(hidden=True):
        for _ in range(max_tokens):
            lm.gen('temp_variable', list_append=True, max_tokens=1)
            print(lm['temp_variable'])
            if not pattern.match(''.join(lm['temp_variable']), partial=True):
                lm.remove('temp_variable')
                return False
            if pattern.match(''.join(lm['temp_variable']), partial=False):
                lm.remove('temp_variable')
                return True

@guidance
def gen_substring(lm, string, name=None, **kwargs):
    # this is obviously not the right implementation, just here so we can explore
    tokens = [lm.endpoint.tokenizer.decode(x) for x in lm.endpoint.tokenizer.encode(string)]
    pattern = f'({"|".join(tokens)})?'
    if name is None:
        name = 'temp_string'
        remove_temp = True
    lm.gen('temp_string', pattern=pattern)
    valid_idxs = [i for i, x in enumerate(tokens) if x == lm['temp_string'] ]
    while valid_idxs:
        next_idxs = [i + 1 for i in valid_idxs if i + 1 < len(tokens)]
        next_tokens = [tokens[i] for i in next_idxs]
        if not next_tokens:
            break
        pattern = f'({"|".join(next_tokens)})?'
        lm.gen('temp_string', pattern=pattern)
        valid_idxs = [i for i in next_idxs if tokens[i] == lm['temp_string']]
    if remove_temp:
        lm.remove('temp_string')
    return lm