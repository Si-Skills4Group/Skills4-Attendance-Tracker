import {
  pgTable,
  serial,
  text,
  integer,
  boolean,
  timestamp,
} from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const tutorsTable = pgTable("tutors", {
  id: serial("id").primaryKey(),
  userId: integer("user_id").notNull().unique(),
  firstName: text("first_name").notNull(),
  lastName: text("last_name").notNull(),
  email: text("email").notNull(),
  employeeRef: text("employee_ref").notNull().unique(),
  active: boolean("active").notNull().default(true),
  externalSystemId: text("external_system_id"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow()
    .$onUpdate(() => new Date()),
});

export const insertTutorSchema = createInsertSchema(tutorsTable).omit({
  id: true,
  createdAt: true,
  updatedAt: true,
});
export type InsertTutor = z.infer<typeof insertTutorSchema>;
export type Tutor = typeof tutorsTable.$inferSelect;
