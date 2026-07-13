import { Router, type IRouter } from "express";
import { and, eq, gte, lte, sql } from "drizzle-orm";
import { db, auditLogsTable, usersTable } from "@workspace/db";
import { ListAuditLogResponse } from "@workspace/api-zod";
import { requireAdmin } from "../lib/auth";

const router: IRouter = Router();

router.get("/audit-log", requireAdmin, async (req, res): Promise<void> => {
  const { entityType, userId, action, dateFrom, dateTo, page, pageSize } =
    req.query as Record<string, string | undefined>;

  const pageNum = page ? parseInt(page, 10) : 1;
  const pageSizeNum = pageSize ? parseInt(pageSize, 10) : 25;

  const filters = [];
  if (entityType) filters.push(eq(auditLogsTable.entityType, entityType));
  if (userId) filters.push(eq(auditLogsTable.userId, parseInt(userId, 10)));
  if (action) filters.push(eq(auditLogsTable.action, action));
  if (dateFrom) filters.push(gte(auditLogsTable.timestamp, new Date(dateFrom)));
  if (dateTo) filters.push(lte(auditLogsTable.timestamp, new Date(dateTo)));

  const whereClause = filters.length > 0 ? and(...filters) : undefined;

  const [rows, [{ count }]] = await Promise.all([
    db
      .select({
        id: auditLogsTable.id,
        userId: auditLogsTable.userId,
        userName: sql<string | null>`concat(${usersTable.firstName}, ' ', ${usersTable.lastName})`,
        action: auditLogsTable.action,
        entityType: auditLogsTable.entityType,
        entityId: auditLogsTable.entityId,
        previousValue: auditLogsTable.previousValue,
        newValue: auditLogsTable.newValue,
        timestamp: auditLogsTable.timestamp,
        ipAddress: auditLogsTable.ipAddress,
      })
      .from(auditLogsTable)
      .leftJoin(usersTable, eq(auditLogsTable.userId, usersTable.id))
      .where(whereClause)
      .orderBy(sql`${auditLogsTable.timestamp} desc`)
      .limit(pageSizeNum)
      .offset((pageNum - 1) * pageSizeNum),
    db.select({ count: sql<number>`count(*)::int` }).from(auditLogsTable).where(whereClause),
  ]);

  res.json(
    ListAuditLogResponse.parse({
      items: rows,
      total: count,
      page: pageNum,
      pageSize: pageSizeNum,
    }),
  );
});

export default router;
