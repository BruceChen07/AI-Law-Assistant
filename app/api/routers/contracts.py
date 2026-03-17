import os
import uuid
import json
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from app.api.dependencies import get_current_user
from app.services.crud import insert_document, insert_contract_audit
from app.services.contract_audit import audit_contract

logger = logging.getLogger("law_assistant")


def build_router(cfg, llm):
    router = APIRouter()

    @router.post("/contracts/audit")
    async def audit_contract_file(
        file: UploadFile = File(...),
        title: str = Form(""),
        language: str = Form("zh"),
        current_user: dict = Depends(get_current_user),
    ):
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".docx", ".pdf"]:
            raise HTTPException(status_code=400, detail="unsupported file type, only docx and pdf are allowed")

        doc_id = str(uuid.uuid4())
        save_path = os.path.join(cfg["files_dir"], f"{doc_id}{ext}")
        with open(save_path, "wb") as f:
            f.write(await file.read())

        file_size = os.path.getsize(save_path)
        insert_document(
            cfg,
            doc_id=doc_id,
            filename=os.path.basename(save_path),
            original_filename=file.filename,
            file_path=save_path,
            file_size=file_size,
            mime_type=getattr(file, "content_type", None),
            user_id=current_user["id"],
            title=title or os.path.basename(file.filename),
            status="active",
            category="contract"
        )

        audit_id = str(uuid.uuid4())
        model_cfg = cfg.get("llm_config") or {}
        logger.info(
            "contract_audit_start audit_id=%s document_id=%s file=%s size=%s lang=%s",
            audit_id,
            doc_id,
            save_path,
            file_size,
            language
        )
        try:
            result = audit_contract(cfg, llm, save_path, lang=language)
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=doc_id,
                status="done",
                result_json=json.dumps(result.get("audit"), ensure_ascii=False),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=datetime.utcnow().isoformat()
            )
            logger.info(
                "contract_audit_done audit_id=%s document_id=%s ocr_used=%s ocr_engine=%s page_count=%s text_length=%s",
                audit_id,
                doc_id,
                (result.get("meta") or {}).get("ocr_used"),
                (result.get("meta") or {}).get("ocr_engine"),
                (result.get("meta") or {}).get("page_count"),
                (result.get("meta") or {}).get("text_length")
            )
        except Exception as e:
            logger.exception(
                "contract_audit_failed audit_id=%s document_id=%s file=%s",
                audit_id,
                doc_id,
                save_path
            )
            insert_contract_audit(
                cfg,
                audit_id=audit_id,
                document_id=doc_id,
                status="failed",
                result_json=json.dumps({"error": str(e)}, ensure_ascii=False),
                model_provider=str(model_cfg.get("provider", "")),
                model_name=str(model_cfg.get("model", "")),
                created_at=datetime.utcnow().isoformat()
            )
            raise HTTPException(status_code=500, detail="contract audit failed")

        return {
            "audit_id": audit_id,
            "document_id": doc_id,
            "result": result.get("audit"),
            "meta": result.get("meta")
        }

    return router
