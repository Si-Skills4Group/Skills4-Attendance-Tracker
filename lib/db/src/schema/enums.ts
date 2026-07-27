import { pgEnum } from "drizzle-orm/pg-core";

export const userRoleEnum = pgEnum("user_role", ["admin", "tutor"]);

export const learnerStatusEnum = pgEnum("learner_status", [
  "active",
  "withdrawn",
  "completed",
  "paused",
]);

export const attendanceStatusEnum = pgEnum("attendance_status", [
  "present",
  "absent_authorised",
  "absent_unauthorised",
  "late",
  "not_expected",
  "withdrawn",
  "bil",
]);

export const deliveryDayEnum = pgEnum("delivery_day", [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
]);
