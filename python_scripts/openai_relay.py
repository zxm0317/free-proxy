from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from dataclasses import dataclass

from .errors import classify_error, is_permanent_unavailable_category
from .config import settings
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


def _trace_relay(event: str, **fields: object) -> None:
    parts = [f'event={event}']
    for key, value in fields.items():
        text = str(value)
        if len(text) > 200:
            text = text[:200] + '...'
        parts.append(f'{key}={text}')
    print('TRACE_RELAY ' + ' '.join(parts), flush=True)


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
        disabled_models_loader=None,
        allowed_models_loader=None,
        route_order_loader=None,
        request_logger=None,
        runtime_model_start=None,
        runtime_model_finish=None,
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
        self.disabled_models_loader = disabled_models_loader
        self.allowed_models_loader = allowed_models_loader
        self.route_order_loader = route_order_loader
        self.request_logger = request_logger
        self.runtime_model_start = runtime_model_start
        self.runtime_model_finish = runtime_model_finish

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
        return payload

    def _adapter_response(self, provider: str, model: str, request: ChatRequest):
        started_at = time.time()
        adapter = self.adapter_factory(provider)
        _trace_relay(
            'adapter_created',
            provider=provider,
            model=model,
            elapsed_ms=int((time.time() - started_at) * 1000),
        )
        payload_started_at = time.time()
        payload = self._payload_for_candidate(provider, model, request)
        _trace_relay(
            'candidate_payload_built',
            provider=provider,
            model=model,
            elapsed_ms=int((time.time() - payload_started_at) * 1000),
        )
        if self.debug_log:
            self.debug_log('upstream_payload_debug', provider=provider, payload=json.dumps(payload, ensure_ascii=False))
        adapter_response = adapter.forward_chat(payload)
        _trace_relay(
            'adapter_forward_done',
            provider=provider,
            model=model,
            elapsed_ms=int((time.time() - started_at) * 1000),
            status=adapter_response.status,
            stream=adapter_response.stream is not None,
        )
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

    def _log_request_async(
        self,
        provider: str,
        model: str,
        status: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        if self.request_logger is None:
            return

        def _write_log() -> None:
            try:
                self.request_logger(provider, model, status, input_tokens, output_tokens, latency_ms, error)
            except Exception:
                pass

        import threading
        threading.Thread(target=_write_log, daemon=True).start()

    @staticmethod
    def _prioritize_interactive_clients(candidates: list[CandidateTarget], request: ChatRequest) -> list[CandidateTarget]:
        return candidates

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
        requested_model = request.requested_model
        manual_order = []
        if self.manual_order_loader:
            step_start = time.time()
            manual_order = self.manual_order_loader()
            _trace_relay('load_manual_order', elapsed_ms=int((time.time() - step_start) * 1000), count=len(manual_order))
        disabled_models = []
        if callable(self.disabled_models_loader):
            step_start = time.time()
            disabled_models = self.disabled_models_loader()
            _trace_relay('load_disabled_models', elapsed_ms=int((time.time() - step_start) * 1000), count=len(disabled_models))
        allowed_models = None
        if callable(self.allowed_models_loader):
            step_start = time.time()
            loaded_allowed = self.allowed_models_loader()
            allowed_models = set(loaded_allowed or [])
            _trace_relay('load_allowed_models', elapsed_ms=int((time.time() - step_start) * 1000), count=len(allowed_models))

        step_start = time.time()
        configured_providers = self.configured_providers_loader()
        _trace_relay('load_configured_providers', elapsed_ms=int((time.time() - step_start) * 1000), count=len(configured_providers), providers=','.join(configured_providers))
        step_start = time.time()
        health_state = self.health_loader()
        _trace_relay('load_health', elapsed_ms=int((time.time() - step_start) * 1000), count=len(health_state))
        now_ts = int(time.time())
        now_local = datetime.fromtimestamp(now_ts).astimezone()
        daily_reset = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
        if now_local < daily_reset:
            daily_reset -= timedelta(days=1)
        daily_reset_ts = int(daily_reset.timestamp())
        health_unavailable_models = [
            key
            for key, entry in health_state.items()
            if (
                '/' in key
                and isinstance(entry, dict)
                and (
                    is_permanent_unavailable_category(str(entry.get('reason') or ''))
                    or (
                        isinstance(entry.get('disabled_until'), int)
                        and int(entry['disabled_until']) > now_ts
                    )
                    or (
                        str(entry.get('reason') or '') in {'network', 'server'}
                        and isinstance(entry.get('checked_at'), int)
                        and int(entry['checked_at']) >= daily_reset_ts
                    )
                )
            )
        ]
        if health_unavailable_models:
            disabled_models = list(dict.fromkeys([*disabled_models, *health_unavailable_models]))
            _trace_relay('skip_unavailable_models', models=','.join(sorted(health_unavailable_models[:20])), count=len(health_unavailable_models))
        step_start = time.time()
        routed_candidates = []
        if (not requested_model or requested_model in {'auto', 'free-proxy/auto', 'free_proxy/auto'}) and callable(self.route_order_loader):
            loaded_route_order = self.route_order_loader()
            routed_candidates = [
                CandidateTarget(provider, model, 'provider_default', index)
                for index, (provider, model) in enumerate(loaded_route_order or [])
            ]
        candidates = self._prioritize_interactive_clients(
            routed_candidates or build_auto_candidates(
                requested_model=requested_model,
                configured=configured_providers,
                health=health_state,
                now_ts=now_ts,
                ttl_seconds=self.health_ttl_seconds,
                manual_order=manual_order,
                disabled_models=disabled_models,
                allowed_models=allowed_models,
            ),
            request,
        )
        _trace_relay('build_candidates', elapsed_ms=int((time.time() - step_start) * 1000), count=len(candidates))
        same_provider_attempts = 0
        current_provider = ''
        listed_loaded: set[str] = set()
        index = 0
        route_build_ms = int((time.time() - overall_start) * 1000)
        error_details = []
        max_total_attempts = max(1, min(len(candidates), settings.max_fallback_attempts))
        attempted_count = 0
        _trace_relay(
            'route_built',
            requested_model=requested_model or 'auto',
            candidates=len(candidates),
            max_total_attempts=max_total_attempts,
            route_build_ms=route_build_ms,
            first_candidates=','.join(f'{item.provider}/{item.model}' for item in candidates[:10]),
        )
        
        while index < len(candidates):
            candidate = candidates[index]
            index += 1
            attempted_count += 1
            start_time = time.time()
            if self.runtime_model_start:
                try:
                    self.runtime_model_start(candidate.provider, candidate.model)
                except Exception:
                    pass
            if candidate.provider == current_provider:
                same_provider_attempts += 1
            else:
                same_provider_attempts = 0
                current_provider = candidate.provider
            _trace_relay(
                'attempt_start',
                attempt=attempted_count,
                candidate_index=index,
                provider=candidate.provider,
                model=candidate.model,
                same_provider_attempts=same_provider_attempts,
            )
            try:
                adapter_response = self._adapter_response(candidate.provider, candidate.model, request)
            except ProviderError as exc:
                error_msg = str(exc)
                elapsed_ms = int((time.time() - start_time) * 1000)
                if self.runtime_model_finish:
                    try:
                        self.runtime_model_finish(candidate.provider, candidate.model, False, elapsed_ms, error_msg)
                    except Exception:
                        pass
                error_details.append(f"{candidate.provider}/{candidate.model}: {error_msg[:100]}")
                failure = classify_error(0, error_msg)
                _trace_relay(
                    'attempt_error',
                    attempt=attempted_count,
                    provider=candidate.provider,
                    model=candidate.model,
                    elapsed_ms=elapsed_ms,
                    category=failure.category,
                    error=error_msg,
                )
                self._record_health(candidate.provider, candidate.model, False, failure.category)
                self._log_request_async(
                    candidate.provider,
                    candidate.model,
                    'error',
                    len(self._prompt_from_messages(request.messages)) // 4,
                    0,
                    int((time.time() - start_time) * 1000),
                    error_msg,
                )
                if failure.category != 'auth' and candidate.provider not in listed_loaded:
                    listed_loaded.add(candidate.provider)
                    candidates = self._append_provider_listed_candidate(candidates, candidate.provider, index)
                decision = decide_next_action(FallbackContext(attempted_count, same_provider_attempts, max_total_attempts=max_total_attempts), RelayAttemptResult(False, candidate.provider, candidate.model, failure.category, None, None, str(exc)))
                _trace_relay(
                    'fallback_decision',
                    attempt=attempted_count,
                    provider=candidate.provider,
                    model=candidate.model,
                    action=decision.action,
                    category=failure.category,
                )
                if decision.action == 'stop':
                    break
                continue
            if adapter_response.status < 400:
                elapsed_ms = int((time.time() - start_time) * 1000)
                _trace_relay(
                    'attempt_ok',
                    attempt=attempted_count,
                    provider=candidate.provider,
                    model=candidate.model,
                    elapsed_ms=elapsed_ms,
                    status=adapter_response.status,
                    stream=adapter_response.stream is not None,
                )
                self._record_health(candidate.provider, candidate.model, True, None, headers=adapter_response.headers)
                _trace_relay(
                    'record_health_done',
                    attempt=attempted_count,
                    provider=candidate.provider,
                    model=candidate.model,
                    elapsed_ms=int((time.time() - start_time) * 1000),
                )
                if self.usage_incrementer:
                    import threading
                    threading.Thread(target=self.usage_incrementer, args=(candidate.provider, candidate.model), daemon=True).start()
                    _trace_relay(
                        'usage_increment_started',
                        attempt=attempted_count,
                        provider=candidate.provider,
                        model=candidate.model,
                        elapsed_ms=int((time.time() - start_time) * 1000),
                    )
                
                if adapter_response.stream is not None:
                    headers_ms = int((time.time() - start_time) * 1000)
                    def _timed_stream(stream, start_time_local, overall_start_local, headers_ms_local, cand_provider, cand_model):
                        first = True
                        accumulated_content = []
                        import json
                        from .response_normalizer import _normalize_tool_calls
                        has_error = False
                        error_msg = None
                        try:
                            for chunk in stream:
                                if first:
                                    first_chunk_ms = int((time.time() - start_time_local) * 1000)
                                    _trace_relay(
                                        'stream_first_chunk',
                                        provider=cand_provider,
                                        model=cand_model,
                                        stream_headers_ms=headers_ms_local,
                                        first_chunk_ms=first_chunk_ms,
                                    )
                                    if self.debug_log:
                                        self.debug_log('route_timing', candidate_order=index, winner=f"{cand_provider}/{cand_model}", stream_headers_ms=headers_ms_local, first_chunk_ms=first_chunk_ms, route_build_ms=route_build_ms)
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
                        except Exception as stream_err:
                            has_error = True
                            error_msg = str(stream_err)
                            raise stream_err
                        finally:
                            if hasattr(stream, 'close'):
                                stream.close()
                            if self.debug_log:
                                total_ms = int((time.time() - overall_start_local) * 1000)
                                self.debug_log('route_timing_total', total_ms=total_ms)
                            if self.request_logger:
                                try:
                                    total_out_tokens = len("".join(accumulated_content)) // 4
                                    status = 'error' if has_error else 'success'
                                    latency_ms = int((time.time() - start_time_local) * 1000)
                                    if self.runtime_model_finish:
                                        try:
                                            self.runtime_model_finish(cand_provider, cand_model, not has_error, latency_ms, error_msg)
                                        except Exception:
                                            pass
                                    self._log_request_async(
                                        cand_provider,
                                        cand_model,
                                        status,
                                        len(self._prompt_from_messages(request.messages)) // 4,
                                        total_out_tokens,
                                        latency_ms,
                                        error_msg,
                                    )
                                except Exception:
                                    pass
                    _trace_relay(
                        'stream_response_ready',
                        provider=candidate.provider,
                        model=candidate.model,
                        attempt_elapsed_ms=int((time.time() - start_time) * 1000),
                        total_ms=int((time.time() - overall_start) * 1000),
                    )
                    return RelayResponse(
                        status=adapter_response.status,
                        headers={
                            'Content-Type': 'text/event-stream; charset=utf-8',
                            'X-Routed-Via': f"{candidate.provider}/{candidate.model}",
                            'X-Fallback-Attempts': str(index - 1)
                        },
                        body=None,
                        stream_chunks=_timed_stream(adapter_response.stream, start_time, overall_start, headers_ms, candidate.provider, candidate.model)
                    )

                if adapter_response.body is None:
                    self._log_request_async(
                        candidate.provider,
                        candidate.model,
                        'success',
                        len(self._prompt_from_messages(request.messages)) // 4,
                        0,
                        int((time.time() - start_time) * 1000),
                        None,
                    )
                    if self.runtime_model_finish:
                        try:
                            self.runtime_model_finish(candidate.provider, candidate.model, True, int((time.time() - start_time) * 1000), None)
                        except Exception:
                            pass
                    return RelayResponse(
                        200,
                        {
                            'Content-Type': 'application/json; charset=utf-8',
                            'X-Routed-Via': f"{candidate.provider}/{candidate.model}",
                            'X-Fallback-Attempts': str(index - 1)
                        },
                        b'',
                        None
                    )
                resp = normalize_provider_response(
                    provider=candidate.provider,
                    model=candidate.model,
                    body=adapter_response.body,
                    stream=request.stream,
                )
                _trace_relay(
                    'normalize_done',
                    attempt=attempted_count,
                    provider=candidate.provider,
                    model=candidate.model,
                    elapsed_ms=int((time.time() - start_time) * 1000),
                    stream=resp.stream_chunks is not None,
                )
                if resp.stream_chunks is not None:
                    headers_ms = int((time.time() - start_time) * 1000)
                    def _timed_stream_local(stream, start_time_local, overall_start_local, headers_ms_local, cand_provider, cand_model):
                        first = True
                        accumulated_content = []
                        has_error = False
                        error_msg = None
                        try:
                            for chunk in stream:
                                if first:
                                    first_chunk_ms = int((time.time() - start_time_local) * 1000)
                                    _trace_relay(
                                        'stream_first_chunk',
                                        provider=cand_provider,
                                        model=cand_model,
                                        stream_headers_ms=headers_ms_local,
                                        first_chunk_ms=first_chunk_ms,
                                    )
                                    if self.debug_log:
                                        self.debug_log('route_timing', candidate_order=index, winner=f"{cand_provider}/{cand_model}", stream_headers_ms=headers_ms_local, first_chunk_ms=first_chunk_ms, route_build_ms=route_build_ms)
                                    first = False
                                try:
                                    decoded = chunk.decode('utf-8', errors='ignore')
                                    if decoded.startswith('data:'):
                                        data_str = decoded[5:].strip()
                                        if data_str != '[DONE]':
                                            parsed = json.loads(data_str)
                                            choices = parsed.get('choices', [])
                                            if choices:
                                                content = choices[0].get('delta', {}).get('content')
                                                if content:
                                                    accumulated_content.append(content)
                                except Exception:
                                    pass
                                yield chunk
                        except Exception as stream_err:
                            has_error = True
                            error_msg = str(stream_err)
                            raise stream_err
                        finally:
                            if hasattr(stream, 'close'):
                                stream.close()
                            if self.debug_log:
                                total_ms = int((time.time() - overall_start_local) * 1000)
                                self.debug_log('route_timing_total', total_ms=total_ms)
                            if self.request_logger:
                                try:
                                    total_out_tokens = len("".join(accumulated_content)) // 4
                                    status = 'error' if has_error else 'success'
                                    latency_ms = int((time.time() - start_time_local) * 1000)
                                    if self.runtime_model_finish:
                                        try:
                                            self.runtime_model_finish(cand_provider, cand_model, not has_error, latency_ms, error_msg)
                                        except Exception:
                                            pass
                                    self._log_request_async(
                                        cand_provider,
                                        cand_model,
                                        status,
                                        len(self._prompt_from_messages(request.messages)) // 4,
                                        total_out_tokens,
                                        latency_ms,
                                        error_msg,
                                    )
                                except Exception:
                                    pass
                    resp = RelayResponse(
                        status=resp.status,
                        headers={
                            **resp.headers,
                            'X-Routed-Via': f"{candidate.provider}/{candidate.model}",
                            'X-Fallback-Attempts': str(index - 1)
                        },
                        body=resp.body,
                        stream_chunks=_timed_stream_local(resp.stream_chunks, start_time, overall_start, headers_ms, candidate.provider, candidate.model)
                    )
                else:
                    try:
                        latency_ms = int((time.time() - start_time) * 1000)
                        try:
                            parsed = json.loads(resp.body.decode('utf-8'))
                            input_tokens = parsed.get('usage', {}).get('prompt_tokens', 0)
                            output_tokens = parsed.get('usage', {}).get('completion_tokens', 0)
                        except Exception:
                            input_tokens = len(self._prompt_from_messages(request.messages)) // 4
                            output_tokens = 0
                        self._log_request_async(
                            candidate.provider,
                            candidate.model,
                            'success',
                            input_tokens,
                            output_tokens,
                            latency_ms,
                            None,
                        )
                        _trace_relay(
                            'request_logger_started',
                            attempt=attempted_count,
                            provider=candidate.provider,
                            model=candidate.model,
                            elapsed_ms=int((time.time() - start_time) * 1000),
                        )
                    except Exception:
                        pass
                    resp = RelayResponse(
                        status=resp.status,
                        headers={
                            **resp.headers,
                            'X-Routed-Via': f"{candidate.provider}/{candidate.model}",
                            'X-Fallback-Attempts': str(index - 1)
                        },
                        body=resp.body,
                        stream_chunks=None
                    )
                    if self.runtime_model_finish:
                        try:
                            self.runtime_model_finish(candidate.provider, candidate.model, True, int((time.time() - start_time) * 1000), None)
                            _trace_relay(
                                'runtime_finish_done',
                                attempt=attempted_count,
                                provider=candidate.provider,
                                model=candidate.model,
                                elapsed_ms=int((time.time() - start_time) * 1000),
                            )
                        except Exception:
                            pass
                _trace_relay(
                    'return_response',
                    attempt=attempted_count,
                    provider=candidate.provider,
                    model=candidate.model,
                    elapsed_ms=int((time.time() - start_time) * 1000),
                    total_ms=int((time.time() - overall_start) * 1000),
                )
                return resp
                
            body_bytes = adapter_response.body or b''
            if not body_bytes and adapter_response.stream is not None:
                body_bytes = b''.join(adapter_response.stream)
            failure = classify_error(adapter_response.status, body_bytes.decode('utf-8', errors='ignore'))
            elapsed_ms = int((time.time() - start_time) * 1000)
            _trace_relay(
                'attempt_http_error',
                attempt=attempted_count,
                provider=candidate.provider,
                model=candidate.model,
                elapsed_ms=elapsed_ms,
                status=adapter_response.status,
                category=failure.category,
                message=failure.message,
            )
            if self.runtime_model_finish:
                try:
                    self.runtime_model_finish(candidate.provider, candidate.model, False, elapsed_ms, failure.message)
                except Exception:
                    pass
            error_details.append(f"{candidate.provider}/{candidate.model} [HTTP {adapter_response.status}]: {failure.message[:200]}")
            if self.debug_log:
                self.debug_log('upstream_error_details', provider=candidate.provider, model=candidate.model, status=adapter_response.status, raw_body=body_bytes.decode('utf-8', errors='ignore'))
            
            self._log_request_async(
                candidate.provider,
                candidate.model,
                'error',
                len(self._prompt_from_messages(request.messages)) // 4,
                0,
                int((time.time() - start_time) * 1000),
                failure.message,
            )

            if failure.category in ('server', 'network', 'rate_limit', 'auth', 'quota', 'model_not_found'):
                self._record_health(candidate.provider, candidate.model, False, failure.category)
                
            if failure.category != 'auth' and candidate.provider not in listed_loaded:
                listed_loaded.add(candidate.provider)
                candidates = self._append_provider_listed_candidate(candidates, candidate.provider, index)
            decision = decide_next_action(FallbackContext(attempted_count, same_provider_attempts, max_total_attempts=max_total_attempts), RelayAttemptResult(False, candidate.provider, candidate.model, failure.category, adapter_response.status, None, failure.message))
            _trace_relay(
                'fallback_decision',
                attempt=attempted_count,
                provider=candidate.provider,
                model=candidate.model,
                action=decision.action,
                category=failure.category,
                status=adapter_response.status,
            )
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
