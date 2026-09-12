CREATE TABLE `room_participants` (
	`id` text PRIMARY KEY NOT NULL,
	`room_id` text NOT NULL,
	`name` text NOT NULL,
	`role` text NOT NULL,
	`state` text DEFAULT 'ready' NOT NULL,
	`latency_ms` integer DEFAULT 0 NOT NULL,
	`joined_at` integer NOT NULL,
	`last_seen_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_room_participants_room_seen` ON `room_participants` (`room_id`,`last_seen_at`);--> statement-breakpoint
CREATE TABLE `room_signals` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`room_id` text NOT NULL,
	`sender_id` text NOT NULL,
	`recipient_id` text NOT NULL,
	`kind` text NOT NULL,
	`payload` text NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_room_signals_recipient_id` ON `room_signals` (`room_id`,`recipient_id`,`id`);--> statement-breakpoint
CREATE TABLE `rooms` (
	`id` text PRIMARY KEY NOT NULL,
	`title` text NOT NULL,
	`conductor_id` text NOT NULL,
	`status` text DEFAULT 'lobby' NOT NULL,
	`start_at` integer,
	`audience_delay_ms` integer DEFAULT 420 NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
