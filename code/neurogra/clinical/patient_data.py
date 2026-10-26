"""Main-owned read-only patient tools. No agent-provided SQL or patient identity."""

from datetime import datetime, timezone

from .schemas import DataFetchResult, RequestContent
from .utils import identity


class PatientDataService:
    def __init__(self, snapshot, config, *, rwe_client=None, rwe_config=None):
        self.snapshot, self.config = snapshot, config
        self.patient_id = snapshot.patient_id
        self.client, self.rwe_config = rwe_client, rwe_config
        self.remote_patient = None
        if self.rwe_config is None and config.rwe_config is not None:
            from .rwe import load_rwe_config
            self.rwe_config = load_rwe_config(config.resolve(config.rwe_config))

    def resolve_patient(self):
        if self.rwe_config is not None and self.remote_patient is None:
            from .rwe import RweClient, local_settings
            self.client = self.client or RweClient(self.rwe_config,
                local_settings(self.config.resolve(self.rwe_config.settings_file)))
            patient = self.client.resolve_patient(self.patient_id)
            if (not isinstance(patient, dict) or patient.get("patient_number", "").strip() != self.patient_id.strip()
                    or not isinstance(patient.get("patient_id"), int) or isinstance(patient.get("patient_id"), bool)):
                raise ValueError("patient_data_binding_failed")
            self.remote_patient = patient
        return {"patient_id": self.patient_id, "case_id": self.snapshot.case_id}

    def list_available_data(self):
        return {"patient_id": self.patient_id, "fields": sorted({o.name for o in self.snapshot.observations}),
                "records": [{"record_id": row.record_id, "kind": row.kind,
                             "status": row.parse_status} for row in self.snapshot.records],
                "forms": ([{'key': key, **form} for key, form in
                            self.snapshot.patient_metadata.get('forms', {}).items()]
                          if self.snapshot.patient_metadata.get('format') == 'rwe_export' else
                          [form.model_dump(mode="json") for form in self.rwe_config.forms]
                          if self.rwe_config else []),
                "data_quality": self.snapshot.patient_metadata.get('data_quality'),
                "observations": [{"observation_id": row.observation_id, "field": row.name,
                                  "domains": row.domains, "time": row.time.model_dump(mode="json"),
                                  "status": row.status, "source_refs": row.source_refs}
                                 for row in self.snapshot.observations],
                "images": [row.model_dump(mode="json") for row in self.snapshot.images]}

    def fetch_patient_data(self, request: RequestContent, request_id: str):
        if request.type != "patient_data":
            raise ValueError("wrong_patient_tool_request")
        if request.form_key is not None:
            if request.form_key in self.snapshot.patient_metadata.get('forms', {}):
                return self._local(request, request_id, request.form_key)
            if self.rwe_config is None and self.snapshot.patient_metadata.get('format') == 'rwe_export':
                return DataFetchResult(request_id=request_id, patient_id=self.patient_id,
                    status='unsupported', error='form_not_in_patient_json')
            return self._remote(request, request_id)
        return self._local(request, request_id)

    def _local(self, request, request_id, form_key=None):
        def matches_form(observation):
            source = observation.context.get('rwe_source', observation.context.get('来源'))
            return isinstance(source, dict) and source.get('form_key') == form_key
        observations = [o for o in self.snapshot.observations if form_key is None or
                        matches_form(o)]
        fields = set(request.requested_fields)
        if not fields and form_key is None:
            return DataFetchResult(request_id=request_id, patient_id=self.patient_id,
                                   status="unsupported", error="explicit_fields_required")
        fields = fields or {o.name for o in observations}
        observations = [o for o in observations if o.name in fields]
        if request.date_from or request.date_to:
            observations = [o for o in observations if o.time.value
                and (not request.date_from or o.time.value[:10] >= request.date_from.isoformat())
                and (not request.date_to or o.time.value[:10] <= request.date_to.isoformat())]
        found = {o.name for o in observations}
        statuses = {}
        for field in fields:
            rows = [o for o in observations if o.name == field]
            statuses[field] = ("found" if any(o.status not in {"not_recorded", "not_performed"} for o in rows)
                               else "not_performed" if rows and all(o.status == "not_performed" for o in rows)
                               else "not_recorded")
        from .utils import fingerprint
        binding = fingerprint({'patient': self.patient_id, 'case': self.snapshot.raw_input_hash,
            'version': self.snapshot.version, 'form': form_key, 'fields': sorted(fields),
            'date_from': str(request.date_from), 'date_to': str(request.date_to),
            'observations': [o.model_dump(mode='json') for o in observations],
            'permissions': {k: v.model_dump() for k, v in self.config.agents.items()},
            'main': self.config.main_agent.model_dump()})
        total = len(observations)
        offset = 0
        if request.cursor:
            try:
                digest, offset_text = request.cursor.rsplit(':', 1)
                offset = int(offset_text)
                if digest != binding or offset < 0 or offset > total:
                    raise ValueError()
            except (ValueError, TypeError):
                return DataFetchResult(request_id=request_id, patient_id=self.patient_id,
                    status='unsupported', error='stale_or_invalid_cursor')
        page_size = request.page_size or 50
        observations = observations[offset:offset + page_size]
        next_cursor = f'{binding}:{offset + len(observations)}' if offset + len(observations) < total else None
        record_ids = {ref for o in observations for ref in o.source_refs}
        records = [r for r in self.snapshot.records if r.record_id in record_ids]
        overall = ("found" if any(s == "found" for s in statuses.values()) else
                   "not_performed" if statuses and all(s == "not_performed" for s in statuses.values()) else "not_recorded")
        return DataFetchResult(request_id=request_id, patient_id=self.patient_id, status=overall,
            next_cursor=next_cursor, total_count=total,
            observations=observations, records=records, field_status=statuses,
            missing_fields=sorted(fields - found | {key for key, value in statuses.items() if value != "found"}))

    def _remote(self, request, request_id):
        if self.rwe_config is None:
            return DataFetchResult(request_id=request_id, patient_id=self.patient_id,
                status="unsupported", source="local", error="external_source_not_configured")
        from .rwe import RweFailure, adapt_export, SYSTEM_FIELDS
        form = next((f for f in self.rwe_config.forms if f.key == request.form_key), None)
        if form is None or any(field in SYSTEM_FIELDS for field in request.requested_fields):
            return DataFetchResult(request_id=request_id, patient_id=self.patient_id,
                status="unsupported", source="rwe", error="rwe_request_not_allowed")
        try:
            self.resolve_patient()
            rows = self.client.fetch_form(form, self.remote_patient["patient_id"])
            if any(not isinstance(row, dict) or str(row.get("patient_id", row.get("patientId"))) !=
                   str(self.remote_patient["patient_id"]) for row in rows):
                raise ValueError("patient_data_binding_failed")
        except RweFailure as exc:
            if "binding" in str(exc):
                raise ValueError("patient_data_binding_failed") from None
            return DataFetchResult(request_id=request_id, patient_id=self.patient_id,
                status="source_unavailable", source="rwe", error=str(exc).split(":")[0])
        if not rows:
            return DataFetchResult(request_id=request_id, patient_id=self.patient_id,
                status="not_recorded", source="rwe", missing_fields=request.requested_fields)
        fields = set(request.requested_fields)
        if fields and not fields & {key for row in rows for key in row}:
            return DataFetchResult(request_id=request_id, patient_id=self.patient_id,
                status="unsupported", source="rwe", error="requested_fields_not_in_form",
                missing_fields=sorted(fields))
        selected = [{key: value for key, value in row.items()
                     if not fields or key in fields or key in SYSTEM_FIELDS or key == form.date_field}
                    for row in rows]
        export = {"export_id": identity("data_export", [self.snapshot.case_id, request_id]),
                  "project_id": self.rwe_config.project_id, "patient": self.remote_patient,
                  "fetched_at": datetime.now(timezone.utc).isoformat(),
                  "forms": [{**form.model_dump(mode="json"), "records": selected}]}
        from .utils import write_json, sha256_file
        source_path = self.config.resolve(self.config.data_root) / 'rwe' / export['export_id'] / 'source.json'
        raw_export = {**export, 'forms': [{**form.model_dump(mode='json'), 'records': rows}]}
        write_json(source_path, raw_export)
        from .intake import ingest_patient
        from .profiling import profile_case
        intake = ingest_patient(adapt_export(export), self.config)
        snapshot = profile_case(intake, [], self.config, None)
        statuses = {key: "found" if any(row.get(key) is not None for row in selected) else "not_recorded"
                    for key in fields}
        return DataFetchResult(request_id=request_id, patient_id=self.patient_id,
            status="found" if (any(value == "found" for value in statuses.values()) if fields else
                                any(row.status not in {"not_recorded", "not_performed"} for row in snapshot.observations))
                   else "not_recorded", source="rwe",
            records=snapshot.records, observations=snapshot.observations, field_status=statuses,
            source_path=str(source_path), source_checksum=sha256_file(source_path),
            missing_fields=sorted(key for key, value in statuses.items() if value != "found"))

    def read_source(self, record_id):
        record = next((r for r in self.snapshot.records if r.record_id == record_id), None)
        if record is None:
            raise ValueError("source_not_in_patient_scope")
        return record.model_copy(deep=True)
