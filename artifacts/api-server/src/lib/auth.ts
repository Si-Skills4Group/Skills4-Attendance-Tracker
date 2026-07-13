import type { Request, Response, NextFunction } from "express";
import bcrypt from "bcryptjs";

export const hashPassword = (password: string): Promise<string> =>
  bcrypt.hash(password, 12);

export const verifyPassword = (
  password: string,
  hash: string,
): Promise<boolean> => bcrypt.compare(password, hash);

export const requireAuth = (
  req: Request,
  res: Response,
  next: NextFunction,
): void => {
  if (!req.session.userId) {
    res.status(401).json({ error: "Not authenticated" });
    return;
  }
  next();
};

export const requireAdmin = (
  req: Request,
  res: Response,
  next: NextFunction,
): void => {
  if (!req.session.userId) {
    res.status(401).json({ error: "Not authenticated" });
    return;
  }
  if (req.session.role !== "admin") {
    res.status(403).json({ error: "Administrator access required" });
    return;
  }
  next();
};
