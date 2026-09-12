import { SwarlinkApp } from "./SwarlinkApp";

export default async function Home({ searchParams }: { searchParams: Promise<{ room?: string }> }) {
  const { room = "" } = await searchParams;
  return <SwarlinkApp initialRoom={room} />;
}
