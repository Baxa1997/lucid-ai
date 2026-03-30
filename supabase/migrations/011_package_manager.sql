-- Add package_manager column to user_settings
-- Stores user's preferred package manager (npm, yarn, pnpm, bun)
-- Used by the AI pipeline when no lock file exists in the workspace

ALTER TABLE user_settings
  ADD COLUMN IF NOT EXISTS package_manager TEXT DEFAULT 'npm';

-- Add check constraint for valid values
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_settings_package_manager_check'
  ) THEN
    ALTER TABLE user_settings
      ADD CONSTRAINT user_settings_package_manager_check
      CHECK (package_manager IN ('npm', 'yarn', 'pnpm', 'bun'));
  END IF;
END $$;
