// ─────────────────────────────────────────────────────────
//  Lucid AI — NextAuth v5 Configuration
//  Google OAuth · Prisma Adapter · Multi-Tenant Sessions
// ─────────────────────────────────────────────────────────

import NextAuth from 'next-auth';
import Google from 'next-auth/providers/google';
import { PrismaAdapter } from '@auth/prisma-adapter';
import prisma from '@/lib/prisma';

export const { handlers, signIn, signOut, auth } = NextAuth({
  // ── Adapter ──────────────────────────────────
  adapter: PrismaAdapter(prisma),

  // ── Providers ────────────────────────────────
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
    // ── MOCK/DEV LOGIN PROVIDER ───────────────────
    {
      id: "credentials",
      name: "Dev Account",
      type: "credentials",
      credentials: {
        email: { label: "Email", type: "email", placeholder: "test@example.com" },
      },
      async authorize(credentials) {
        console.log("LOGIN ATTEMPT:", credentials?.email);
        try {
          if (!credentials?.email) return null;

          const email = credentials.email;

          // Find or create user
          let user = await prisma.user.findUnique({ where: { email } });

          if (!user) {
            console.log("Creating new user for:", email);
            user = await prisma.user.create({
              data: {
                email,
                name: email.split('@')[0],
                image: `https://api.dicebear.com/7.x/avataaars/svg?seed=${email}`,
              },
            });
          } else {
            console.log("Found existing user:", email);
          }

          return {
            id: user.id,
            name: user.name,
            email: user.email,
            image: user.image,
          };

        } catch (e) {
          console.error("LOGIN ERROR:", e);
          return null;
        }
      },
    },
  ],

  // ── Session Strategy ─────────────────────────
  session: { strategy: "jwt" },

  // ── Pages ────────────────────────────────────
  pages: {
    signIn: '/login',
    error: '/login',
  },

  // ── Callbacks ────────────────────────────────
  // ── Callbacks ────────────────────────────────
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.id = user.id;
      }
      return token;
    },

    async session({ session, token }) {
      if (token) {
        session.user.id = token.id;
      }
      return session;
    },
  },

});
