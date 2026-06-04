from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .errors import classify_error
from .fallback_policy import FallbackContext, decide_next_action
from .provider_catalog import configured_provider_names, get_model_capabilities, get_provider
from .provider_errors import ProviderError
from .provider_routing import CandidateTarget, build_auto_candidates
from .protocol_converter import gemini_json_to_openai_chat
from .request_normalizer import ChatRequest, normalize_chat_request
from .response_normalizer import RelayResponse, normalize_provider_response, sanitize_model_text
from .token_policy import DEFAULT_POLICY, TokenPolicy, model_default_output_tokens, response_token_budget, trim_prompt


@dataclass(frozen=True)
class RelayAttemptResult:
    ok: bool
    provider: str
    model: str
    category: str | None
    status: int | None
    response: RelayResponse | None
    error: str | None


class OpenAIRelay:
    def __init__(
        self,
        *,
        adapter_factory,
        health_loader,
        health_updater=None,
        preferred_model_loader=None,
        health_ttl_seconds: int,
        configured_providers_loader=configured_provider_names,
        debug_log=None,
        usage_incrementer=None,
        manual_order_loader=None,
    ) -> None:
        self.adapter_factory = adapter_factory
        self.health_loader = health_loader
        self.health_updater = health_updater
        self.preferred_model_loader = preferred_model_loader
        self.health_ttl_seconds = health_ttl_seconds
        self.configured_providers_loader = configured_providers_loader
        self.debug_log = debug_log
        self.usage_incrementer = usage_incrementer
        self.manual_order_loader = manual_order_loader

    def normalize(self, payload: dict[str, object]) -> ChatRequest:
        return normalize_chat_request(payload)

    @staticmethod
    def _prompt_from_messages(messages: list[dict[str, object]]) -> str:
        parts: list[str] = []
        for item in messages:
            content = item.get('content')
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
                continue
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get('text')
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
        merged = '\n'.join(parts).strip()
        return merged or 'ok'

    @staticmethod
    def _trim_message_content(provider: str, message: dict[str, object]) -> dict[str, object]:
        trimmed = dict(message)
        content = trimmed.get('content')
        if isinstance(content, str) and content.strip():
            trimmed['content'] = trim_prompt(provider, content.strip())
        elif isinstance(content, list):
            blocks: list[dict[str, object]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                copied = dict(block)
                text = copied.get('text')
                if isinstance(text, str) and text.strip():
                    copied['text'] = trim_prompt(provider, text.strip())
                blocks.append(copied)
            if blocks:
                trimmed['content'] = blocks
        return trimmed

    @staticmethod
    def _message_content_length(message: dict[str, object]) -> int:
        content = message.get('content')
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list):
            total = 0
            for block in content:
                if isinstance(block, dict):
                    text = block.get('text')
                    if isinstance(text, str):
                        total += len(text)
            return total
        return 0

    @classmethod
    def _trim_messages_for_provider(cls, provider: str, messages: list[dict[str, object]]) -> list[dict[str, object]]:
        from .config import settings
        policy = DEFAULT_POLICY.get(provider, TokenPolicy(max_input_chars=settings.max_input_chars, reserve_output_tokens=256))
        if not messages:
            return []

        trimmed_messages = [cls._trim_message_content(provider, message) for message in messages]
        total_chars = sum(cls._message_content_length(message) for message in trimmed_messages)
        if total_chars <= policy.max_input_chars:
            return trimmed_messages

        system_prefix: list[dict[str, object]] = []
        tail: list[dict[str, object]] = []
        prefix_done = False
        for message in trimmed_messages:
            role = str(message.get('role', '')).strip()
            if not prefix_done and role == 'system':
                system_prefix.append(message)
                continue
            prefix_done = True
            tail.append(message)

        system_budget = max(1024, policy.max_input_chars // 4)
        tail_budget = max(1024, policy.max_input_chars - system_budget)
        kept: list[dict[str, object]] = []

        selected_tail: list[dict[str, object]] = []
        remaining_tail_budget = tail_budget
        for message in reversed(tail):
            message_length = cls._message_content_length(message)
            if selected_tail and remaining_tail_budget - message_length < 0:
                break
            selected_tail.append(message)
            remaining_tail_budget -= message_length
            if remaining_tail_budget <= 0:
                break

        if not selected_tail and tail:
            selected_tail.append(tail[-1])

        remaining_system_budget = system_budget
        for message in system_prefix:
            message_length = cls._message_content_length(message)
            if kept and remaining_system_budget - message_length < 0:
                break
            kept.append(message)
            remaining_system_budget -= message_length
            if remaining_system_budget <= 0:
                break

        if not kept and system_prefix:
            kept.append(system_prefix[0])

        kept.extend(reversed(selected_tail))
        
        # FINAL SAFETY CHECK: ensure total length does not exceed policy.max_input_chars
        final_messages = kept or trimmed_messages[-1:]
        total_len = sum(cls._message_content_length(m) for m in final_messages)
        excess = total_len - policy.max_input_chars
        
        if excess > 0:
            for i in range(len(final_messages) - 1, -1, -1):
                msg = final_messages[i]
                if excess <= 0:
                    break
                    
                if isinstance(msg.get('content'), str):
                    current_len = len(msg['content'])
                    if current_len == 0:
                        continue
                    cut_amount = min(current_len, excess + 50)
                    new_content = msg['content'][:max(0, current_len - cut_amount)]
                    if new_content:
                        new_content += '...[截断]'
                    final_messages[i] = dict(msg)
                    final_messages[i]['content'] = new_content
                    excess -= (current_len - len(new_content))
                elif isinstance(msg.get('content'), list):
                    blocks = list(msg['content'])
                    for j in range(len(blocks) - 1, -1, -1):
                        block = blocks[j]
                        if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str):
                            text = block['text']
                            current_len = len(text)
                            if current_len == 0:
                                continue
                            cut_amount = min(current_len, excess + 50)
                            new_text = text[:max(0, current_len - cut_amount)]
                            if new_text:
                                new_text += '...[截断]'
                            blocks[j] = dict(block)
                            blocks[j]['text'] = new_text
                            excess -= (current_len - len(new_text))
                            if excess <= 0:
                                break
                    final_messages[i] = dict(msg)
                    final_messages[i]['content'] = blocks
                    
        return final_messages

    def _payload_for_candidate(self, provider: str, model: str, request: ChatRequest) -> dict[str, object]:
        payload = dict(request.raw_payload)
        payload.pop('requested_model', None)
        payload.pop('client_hint', None)
        payload.pop('provider', None)
        payload['model'] = model
        payload['stream'] = request.stream
        payload['messages'] = self._trim_messages_for_provider(provider, request.messages)
        default_output = model_default_output_tokens(provider, model, response_token_budget(provider))
        requested_output = request.max_output_tokens if isinstance(request.max_output_tokens, int) and request.max_output_tokens > 0 else default_output
        final_max = min(requested_output, default_output)
        # If max_tokens is very large, some strict providers (like GitHub Copilot) will throw HTTP 413
        # if prompt + max_tokens exceeds the hard limit. Popping max_tokens allows the provider 
        # to dynamically fill the remaining context window without rejecting the request.
        if final_max > 2048 and provider in ('github', 'gemini'):
            payload.pop('max_tokens', None)
            payload.pop('max_completion_tokens', None)
        else:
            payload['max_tokens'] = final_max
            
        # VERY IMPORTANT: If Hermes sends 60+ tools, the tools JSON alone can exceed 20k characters.
        # GitHub Copilot's 8192 token window immediately throws 413 Payload Too Large.
        # We strip tools for github to prove if this is the cause of the 413.
        if provider == 'github':
            payload.pop('tools', None)
            payload.pop('tool_choice', None)
            
        return payload

    def _adapter_response(self, provider: str, model: str, request: ChatRequest):
        adapter = self.adapter_factory(provider)
        payload = self._payload_for_candidate(provider, model, request)
        if self.debug_log:
            self.debug_log('upstream_payload_debug', provider=provider, payload=json.dumps(payload, ensure_ascii=False))
        adapter_response = adapter.forward_chat(payload)
        if get_provider(provider).format == 'gemini' and adapter_response.status < 400 and adapter_response.body is not None:
            body = adapter_response.body or b''
            parsed = json.loads(body.decode('utf-8'))
            return type(
                'AdapterResponse',
                (),
                {
                    'status': adapter_response.status,
                    'headers': {'Content-Type': 'application/json; charset=utf-8'},
                    'body': gemini_json_to_openai_chat(provider, model, parsed),
                    'stream': None,
                    'content_type': 'application/json; charset=utf-8',
                },
            )()
        return adapter_response

    def _record_health(self, provider: str, model: str, ok: bool, reason: str | None = None, headers: dict[str, str] | None = None) -> None:
        if self.health_updater is None:
            return
        self.health_updater(provider, model, ok, reason, headers)

    @staticmethod
    def _prioritize_interactive_clients(candidates: list[CandidateTarget], request: ChatRequest) -> list[CandidateTarget]:
        client_hint = str(request.raw_payload.get('client_hint', '')).strip().lower()
        if client_hint not in {'opencode', 'openclaw'}:
            return candidates
        provider_priority = {'longcat': 0}
        return sorted(
            candidates,
            key=lambda item: (
                provider_priority.get(item.provider, 1),
                -int(get_model_capabilities(item.provider, item.model).get('default_output_tokens', 0) or 0),
                item.rank,
            ),
        )

    def _append_provider_listed_candidate(self, candidates: list[CandidateTarget], provider: str, insert_at: int) -> list[CandidateTarget]:
        ordered = list(candidates)
        seen = {(item.provider, item.model) for item in ordered}
        adapter = self.adapter_factory(provider)
        list_models = getattr(adapter, 'list_models', None)
        if not callable(list_models):
            return ordered
        try:
            models = list_models()
        except Exception:
            return ordered
        if not models:
            return ordered
        listed_model = models[0]
        key = (provider, listed_model)
        if key in seen:
            return ordered
        ordered.insert(insert_at, CandidateTarget(provider, listed_model, 'provider_default', insert_at))
        return [CandidateTarget(item.provider, item.model, item.source, rank) for rank, item in enumerate(ordered)]

    @staticmethod
    def _extract_openai_text(parsed: object) -> str:
        if not isinstance(parsed, dict):
            return ''
        choices = parsed.get('choices')
        if not isinstance(choices, list) or not choices:
            return ''
        first = choices[0]
        if not isinstance(first, dict):
            return ''
        message = first.get('message')
        if isinstance(message, dict):
            raw_content = message.get('content')
            if isinstance(raw_content, str) and raw_content.strip():
                return sanitize_model_text(raw_content.strip())
            reasoning = message.get('reasoning_content')
            if isinstance(reasoning, str) and reasoning.strip():
                return sanitize_model_text(reasoning.strip())
            if isinstance(raw_content, list):
                chunks: list[str] = []
                for item in raw_content:
                    if isinstance(item, dict):
                        text = item.get('text')
                        if isinstance(text, str) and text.strip():
                            chunks.append(sanitize_model_text(text.strip()))
                merged = '\n'.join(chunks).strip()
                if merged:
                    return merged
        text = first.get('text')
        if isinstance(text, str) and text.strip():
            return sanitize_model_text(text.strip())
        return ''

    def handle_chat(self, request: ChatRequest) -> RelayResponse:
        overall_start = time.time()
        preferred_model = ''
        if callable(self.preferred_model_loader):
            preferred_model = str(self.preferred_model_loader() or '').strip()
        requested_model = request.requested_model or (preferred_model if preferred_model else None)
        manual_order = []
        if self.manual_order_loader:
            manual_order = self.manual_order_loader()
        candidates = self._prioritize_interactive_clients(
            build_auto_candidates(
                requested_model=requested_model,
                configured=self.configured_providers_loader(),
                health=self.health_loader(),
                now_ts=int(time.time()),
                ttl_seconds=self.health_ttl_seconds,
                manual_order=manual_order,
            ),
            request,
        )
        same_provider_attempts = 0
        current_provider = ''
        listed_loaded: set[str] = set()
        index = 0
        route_build_ms = int((time.time() - overall_start) * 1000)
        error_details = []
        
        while index < len(candidates):
            candidate = candidates[index]
            index += 1
            start_time = time.time()
            if candidate.provider == current_provider:
                same_provider_attempts += 1
            else:
                same_provider_attempts = 0
                current_provider = candidate.provider
            try:
                adapter_response = self._adapter_response(candidate.provider, candidate.model, request)
            except ProviderError as exc:
                error_msg = str(exc)
                error_details.append(f"{candidate.provider}/{candidate.model}: {error_msg[:100]}")
                failure = classify_error(0, error_msg)
                self._record_health(candidate.provider, candidate.model, False, failure.category)
                if candidate.provider not in listed_loaded:
                    listed_loaded.add(candidate.provider)
                    candidates = self._append_provider_listed_candidate(candidates, candidate.provider, index)
                decision = decide_next_action(FallbackContext(index, same_provider_attempts), RelayAttemptResult(False, candidate.provider, candidate.model, failure.category, None, None, str(exc)))
                if decision.action == 'stop':
                    break
                continue
            if adapter_response.status < 400:
                self._record_health(candidate.provider, candidate.model, True, None, headers=adapter_response.headers)
                if self.usage_incrementer:
                    import threading
                    threading.Thread(target=self.usage_incrementer, args=(candidate.provider, candidate.model), daemon=True).start()
                
                if adapter_response.stream is not None:
                    headers_ms = int((time.time() - start_time) * 1000)
                    def _timed_stream(stream, start_time_local, overall_start_local, headers_ms_local, cand_provider):
                        first = True
                        accumulated_content = []
                        import json
                        from .response_normalizer import _normalize_tool_calls
                        try:
                            for chunk in stream:
                                if first:
                                    first_chunk_ms = int((time.time() - start_time_local) * 1000)
                                    if self.debug_log:
                                        self.debug_log('route_timing', candidate_order=index, winner=f"{candidate.provider}/{candidate.model}", stream_headers_ms=headers_ms_local, first_chunk_ms=first_chunk_ms, route_build_ms=route_build_ms)
                                    first = False
                                    
                                if not chunk.strip():
                                    yield chunk
                                    continue
                                    
                                decoded = chunk.decode('utf-8', errors='ignore')
                                if not decoded.startswith('data:'):
                                    yield chunk
                                    continue
                                    
                                data_str = decoded[5:].strip()
                                if data_str == '[DONE]':
                                    yield chunk
                                    continue
                                    
                                try:
                                    parsed_json = json.loads(data_str)
                                    choices = parsed_json.get('choices', [])
                                    # Strict clients crash if choices is empty
                                    if not choices:
                                        continue
                                        
                                    delta = choices[0].get('delta', {})
                                    content = delta.get('content')
                                    if content:
                                        accumulated_content.append(content)
                                        
                                    finish_reason = choices[0].get('finish_reason')
                                    if finish_reason == 'stop':
                                        full_content = "".join(accumulated_content)
                                        parsed_tc = _normalize_tool_calls(cand_provider, full_content)
                                        if parsed_tc is not None:
                                            choices[0]['finish_reason'] = 'tool_calls'
                                            if 'delta' not in choices[0]:
                                                choices[0]['delta'] = {}
                                                
                                            # In SSE streaming, tool_calls must have an 'index' field
                                            stream_tool_calls = []
                                            for i, tc in enumerate(parsed_tc.tool_calls):
                                                stc = dict(tc)
                                                stc['index'] = i
                                                stream_tool_calls.append(stc)
                                                
                                            choices[0]['delta']['tool_calls'] = stream_tool_calls
                                            rewritten = f"data: {json.dumps(parsed_json, ensure_ascii=False)}\n\n".encode('utf-8')
                                            yield rewritten
                                            continue
                                except Exception:
                                    pass
                                yield chunk
                        finally:
                            if hasattr(stream, 'close'):
                                stream.close()
                            if self.debug_log:
                                total_ms = int((time.time() - overall_start_local) * 1000)
                                self.debug_log('route_timing_total', total_ms=total_ms)
                    return RelayResponse(
                        status=adapter_response.status,
                        headers={'Content-Type': 'text/event-stream; charset=utf-8'},
                        body=None,
                        stream_chunks=_timed_stream(adapter_response.stream, start_time, overall_start, headers_ms, candidate.provider)
                    )

                if adapter_response.body is None:
                    return RelayResponse(200, {'Content-Type': 'application/json; charset=utf-8'}, b'', None)
                resp = normalize_provider_response(
                    provider=candidate.provider,
                    model=candidate.model,
                    body=adapter_response.body,
                    stream=request.stream,
                )
                if resp.stream_chunks is not None:
                    headers_ms = int((time.time() - start_time) * 1000)
                    def _timed_stream_local(stream, start_time_local, overall_start_local, headers_ms_local):
                        first = True
                        try:
                            for chunk in stream:
                                if first:
                                    first_chunk_ms = int((time.time() - start_time_local) * 1000)
                                    if self.debug_log:
                                        self.debug_log('route_timing', candidate_order=index, winner=f"{candidate.provider}/{candidate.model}", stream_headers_ms=headers_ms_local, first_chunk_ms=first_chunk_ms, route_build_ms=route_build_ms)
                                    first = False
                                yield chunk
                        finally:
                            if hasattr(stream, 'close'):
                                stream.close()
                            if self.debug_log:
                                total_ms = int((time.time() - overall_start_local) * 1000)
                                self.debug_log('route_timing_total', total_ms=total_ms)
                    resp = RelayResponse(
                        status=resp.status,
                        headers=resp.headers,
                        body=resp.body,
                        stream_chunks=_timed_stream_local(resp.stream_chunks, start_time, overall_start, headers_ms)
                    )
                return resp
                
            body_bytes = adapter_response.body or b''
            if not body_bytes and adapter_response.stream is not None:
                body_bytes = b''.join(adapter_response.stream)
            failure = classify_error(adapter_response.status, body_bytes.decode('utf-8', errors='ignore'))
            error_details.append(f"{candidate.provider}/{candidate.model} [HTTP {adapter_response.status}]: {failure.message[:200]}")
            if self.debug_log:
                self.debug_log('upstream_error_details', provider=candidate.provider, model=candidate.model, status=adapter_response.status, raw_body=body_bytes.decode('utf-8', errors='ignore'))
            
            # ONLY melt the model if the error is a genuine provider/availability issue.
            # Client errors like token_limit (413) or unknown (400 invalid param) should NOT melt the model globally.
            if failure.category in ('server', 'network', 'rate_limit', 'auth', 'quota', 'model_not_found'):
                self._record_health(candidate.provider, candidate.model, False, failure.category)
                
            if candidate.provider not in listed_loaded:
                listed_loaded.add(candidate.provider)
                candidates = self._append_provider_listed_candidate(candidates, candidate.provider, index)
            decision = decide_next_action(FallbackContext(index, same_provider_attempts), RelayAttemptResult(False, candidate.provider, candidate.model, failure.category, adapter_response.status, None, failure.message))
            if decision.action == 'stop':
                break
        
        detail_msg = "all candidates failed"
        if error_details:
            detail_msg += ". Details: " + " | ".join(error_details)
            
        error_body = json.dumps(
            {
                'error': {
                    'message': detail_msg,
                    'type': 'server_error',
                    'param': None,
                    'code': '502',
                }
            },
            ensure_ascii=False,
        ).encode('utf-8')
        return RelayResponse(502, {'Content-Type': 'application/json; charset=utf-8'}, error_body, None)
