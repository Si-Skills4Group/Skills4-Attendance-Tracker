import { pgTable, serial, text, numeric } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

// Singleton table -- exactly one row is expected to exist (id = 1).
export const appSettingsTable = pgTable("app_settings", {
  id: serial("id").primaryKey(),
  organisationName: text("organisation_name").notNull().default("Skills4Group"),
  lowAttendanceThreshold: numeric("low_attendance_threshold", {
    mode: "number",
  })
    .notNull()
    .default(85),
});

export const insertAppSettingsSchema = createInsertSchema(
  appSettingsTable,
).omit({ id: true });
export type InsertAppSettings = z.infer<typeof insertAppSettingsSchema>;
export type AppSettings = typeof appSettingsTable.$inferSelect;
