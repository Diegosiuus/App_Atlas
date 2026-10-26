import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function jsonResponse(body: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }
  if (request.method !== "POST") {
    return jsonResponse({ error: "Method not allowed" }, 405);
  }

  const authorization = request.headers.get("Authorization") ?? "";
  const accessToken = authorization.replace(/^Bearer\s+/i, "");
  if (!accessToken) return jsonResponse({ error: "Authentication required" }, 401);

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const resendApiKey = Deno.env.get("RESEND_API_KEY");
  const inboxEmail = Deno.env.get("SUPPORT_INBOX_EMAIL");
  const fromEmail = Deno.env.get("SUPPORT_FROM_EMAIL");
  if (!supabaseUrl || !serviceRoleKey || !resendApiKey || !inboxEmail || !fromEmail) {
    return jsonResponse({ error: "Support email is not configured" }, 503);
  }

  let ticketId: number;
  try {
    const body = await request.json();
    ticketId = Number(body.ticket_id);
    if (!Number.isSafeInteger(ticketId) || ticketId < 1) {
      return jsonResponse({ error: "Invalid ticket id" }, 400);
    }
  } catch {
    return jsonResponse({ error: "Invalid JSON body" }, 400);
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
  const { data: authData, error: authError } = await supabase.auth.getUser(accessToken);
  if (authError || !authData.user) return jsonResponse({ error: "Invalid session" }, 401);

  const { data: ticket, error: ticketError } = await supabase
    .from("support_tickets")
    .select("id, user_id, category, subject, message, contact_email, created_at")
    .eq("id", ticketId)
    .eq("user_id", authData.user.id)
    .single();
  if (ticketError || !ticket) return jsonResponse({ error: "Ticket not found" }, 404);

  const { data: profile } = await supabase
    .from("profiles")
    .select("username")
    .eq("user_id", authData.user.id)
    .maybeSingle();

  const categoryLabel = ticket.category === "bug"
    ? "Problema"
    : ticket.category === "idea"
    ? "Sugerencia"
    : "Otro";
  const text = [
    `Tipo: ${categoryLabel}`,
    `Asunto: ${ticket.subject}`,
    `Usuario: ${profile?.username ?? "No disponible"}`,
    `Fecha: ${ticket.created_at}`,
    `Correo de respuesta: ${ticket.contact_email ?? "No indicado"}`,
    "",
    ticket.message,
  ].join("\n");

  const emailResponse = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${resendApiKey}`,
      "Content-Type": "application/json",
      "Idempotency-Key": `atlas-support-ticket-${ticket.id}`,
    },
    body: JSON.stringify({
      from: fromEmail,
      to: [inboxEmail],
      subject: `[Atlas Earth Calc] ${categoryLabel}: ${ticket.subject}`,
      text,
    }),
  });

  if (!emailResponse.ok) {
    return jsonResponse({ error: "Email provider rejected the notification" }, 502);
  }
  return jsonResponse({ email_sent: true });
});
