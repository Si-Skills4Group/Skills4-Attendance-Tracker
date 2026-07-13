import {
  pgTable,
  serial,
  text,
  integer,
  numeric,
  timestamp,
  unique,
} from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";
import { attendanceStatusEnum } from "./enums";

export const attendanceRecordsTable = pgTable(
  "attendance_records",
  {
    id: serial("id").primaryKey(),
    sessionId: integer("session_id").notNull(),
    learnerId: integer("learner_id").notNull(),
    status: attendanceStatusEnum("status").notNull(),
    hoursAttended: numeric("hours_attended", { mode: "number" })
      .notNull()
      .default(0),
    minutesLate: integer("minutes_late").notNull().default(0),
    notes: text("notes"),
    overrideReason: text("override_reason"),
    lastEditedBy: integer("last_edited_by"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow()
      .$onUpdate(() => new Date()),
  },
  (table) => [unique().on(table.sessionId, table.learnerId)],
);

export const insertAttendanceRecordSchema = createInsertSchema(
  attendanceRecordsTable,
).omit({ id: true, createdAt: true, updatedAt: true });
export type InsertAttendanceRecord = z.infer<
  typeof insertAttendanceRecordSchema
>;
export type AttendanceRecord = typeof attendanceRecordsTable.$inferSelect;
