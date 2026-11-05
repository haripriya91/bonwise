import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

export const supabase =
  supabaseUrl && publishableKey ? createClient(supabaseUrl, publishableKey) : null;

export const hasSupabaseConfig = Boolean(supabase);
