import { beforeEach, describe, expect, it } from "vitest"

import {
  buildAuditSnapshot,
  clearCurrentAuditSnapshot,
  createAuditTaskId,
  getAuditUserScope,
  getAuditSnapshotStorageKeys,
  listAuditSnapshots,
  loadAuditSnapshotByTaskId,
  loadCurrentAuditSnapshot,
  saveCurrentAuditSnapshot,
} from "./auditPersistence"

describe("auditPersistence", () => {
  const userA = { id: 1, username: "alice", role: "auditor" }
  const userB = { id: 2, username: "bob", role: "auditor" }

  beforeEach(() => {
    localStorage.clear()
  })

  it("restores a saved audit snapshot", () => {
    const taskId = createAuditTaskId()
    const snapshot = buildAuditSnapshot({
      taskId,
      auditId: taskId,
      documentId: "doc-1",
      contract: {
        title: "Test Contract",
        language: "zh",
        auditMode: "rag",
        region: "CN",
        date: "2026-10-15",
        industry: "Retail",
        taxFocus: true,
      },
      contractResult: {
        summary: "done",
        risks: [{ level: "medium", issue: "invoice risk" }],
      },
      previewMode: "text",
      previewText: "preview body",
      contractProgress: 100,
      contractProgressStage: "done",
    })
    expect(saveCurrentAuditSnapshot(snapshot, userA)).toMatchObject({ ok: true })

    const restored = loadCurrentAuditSnapshot(userA)
    expect(restored.noticeCode).toBe("")
    expect(restored.snapshot?.documentId).toBe("doc-1")
    expect(restored.snapshot?.contractResult?.summary).toBe("done")
    expect(restored.snapshot?.previewText).toBe("preview body")
    expect(restored.snapshot?.userScope).toBe(getAuditUserScope(userA))
  })

  it("clears corrupted snapshot and reports a restore warning", () => {
    const taskId = "broken-task"
    const keys = getAuditSnapshotStorageKeys(taskId, userA)
    localStorage.setItem(keys.currentTaskKey, taskId)
    localStorage.setItem(keys.taskKey, "{broken json")

    const restored = loadCurrentAuditSnapshot(userA)
    expect(restored.snapshot).toBeNull()
    expect(restored.noticeCode).toBe("restore_invalid")
    expect(localStorage.getItem(keys.currentTaskKey)).toBeNull()
  })

  it("keeps only the latest snapshots when storage is trimmed", () => {
    const ids = ["task-1", "task-2", "task-3", "task-4", "task-5", "task-6", "task-7", "task-8", "task-9"]
    ids.forEach((taskId, index) => {
      const snapshot = buildAuditSnapshot({
        taskId,
        auditId: taskId,
        documentId: `doc-${index + 1}`,
        contract: { title: `Contract ${index + 1}` },
      })
      expect(saveCurrentAuditSnapshot(snapshot, userA).ok).toBe(true)
    })

    expect(localStorage.getItem(getAuditSnapshotStorageKeys("task-1", userA).taskKey)).toBeNull()
    expect(loadCurrentAuditSnapshot(userA).snapshot?.taskId).toBe("task-9")
    expect(listAuditSnapshots(userA)).toHaveLength(8)
  })

  it("clears only the current task snapshot", () => {
    const current = buildAuditSnapshot({ taskId: "task-current", auditId: "task-current", documentId: "doc-current" })
    const older = buildAuditSnapshot({ taskId: "task-old", auditId: "task-old", documentId: "doc-old" })
    saveCurrentAuditSnapshot(older, userA)
    saveCurrentAuditSnapshot(current, userA)

    clearCurrentAuditSnapshot(userA)

    expect(loadCurrentAuditSnapshot(userA).snapshot).toBeNull()
    expect(localStorage.getItem(getAuditSnapshotStorageKeys("task-old", userA).taskKey)).not.toBeNull()
  })

  it("isolates snapshot history by logged-in user identity", () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "alice-1",
      auditId: "alice-1",
      documentId: "doc-a1",
      contract: { title: "Alice Contract" },
      contractResult: { summary: "alice only", risks: [{ level: "high", issue: "A" }] },
    }), userA)
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "bob-1",
      auditId: "bob-1",
      documentId: "doc-b1",
      contract: { title: "Bob Contract" },
      contractResult: { summary: "bob only", risks: [{ level: "low", issue: "B" }] },
    }), userB)

    const aliceHistory = listAuditSnapshots(userA)
    const bobHistory = listAuditSnapshots(userB)

    expect(aliceHistory).toHaveLength(1)
    expect(bobHistory).toHaveLength(1)
    expect(aliceHistory[0].title).toBe("Alice Contract")
    expect(bobHistory[0].title).toBe("Bob Contract")
    expect(loadCurrentAuditSnapshot(userA).snapshot?.taskId).toBe("alice-1")
    expect(loadCurrentAuditSnapshot(userB).snapshot?.taskId).toBe("bob-1")
  })

  it("loads a specific historical snapshot for compare and restore flows", () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "alice-old",
      auditId: "alice-old",
      documentId: "doc-old",
      contract: { title: "Old Audit" },
      contractResult: { summary: "old summary", risks: [{ level: "medium", issue: "legacy" }] },
    }), userA)
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "alice-new",
      auditId: "alice-new",
      documentId: "doc-new",
      contract: { title: "New Audit" },
      contractResult: { summary: "new summary", risks: [{ level: "high", issue: "delta" }] },
    }), userA)

    const restored = loadAuditSnapshotByTaskId(userA, "alice-old")
    expect(restored.snapshot?.contract?.title).toBe("Old Audit")
    expect(loadCurrentAuditSnapshot(userA).snapshot?.taskId).toBe("alice-old")
  })
})
