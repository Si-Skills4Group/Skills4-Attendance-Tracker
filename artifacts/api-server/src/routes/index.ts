import { Router, type IRouter } from "express";
import healthRouter from "./health";
import authRouter from "./auth";
import dashboardRouter from "./dashboard";
import tutorsRouter from "./tutors";
import learnersRouter from "./learners";
import cohortsRouter from "./cohorts";
import allocationRouter from "./allocation";
import attendanceRouter from "./attendance";
import reportsRouter from "./reports";
import auditRouter from "./audit";
import settingsRouter from "./settings";

const router: IRouter = Router();

router.use(healthRouter);
router.use(authRouter);
router.use(dashboardRouter);
router.use(tutorsRouter);
router.use(learnersRouter);
router.use(cohortsRouter);
router.use(allocationRouter);
router.use(attendanceRouter);
router.use(reportsRouter);
router.use(auditRouter);
router.use(settingsRouter);

export default router;
