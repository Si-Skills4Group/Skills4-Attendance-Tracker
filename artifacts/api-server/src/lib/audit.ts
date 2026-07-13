import type { Request } from "express";
import { db, auditLogsTable } from "@workspace/db";

export const writeAuditLog = async (
  req: Request,
  entry: {
    action: string;
    entityType: string;
    entityId?: number | null;
    previousValue?: unknown;
    newValue?: unknown;
  },
): Promise<void> => {
  await db.insert(auditLogsTable).values({
    userId: req.session.userId ?? null,
    action: entry.action,
    entityType: entry.entityType,
    entityId: entry.entityId ?? null,
    previousValue:
      entry.previousValue !== undefined
        ? JSON.stringify(entry.previousValue)
        : null,
    newValue:
      entry.newValue !== undefined ? JSON.stringify(entry.newValue) : null,
    ipAddress: req.ip ?? null,
  });
};
