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
          
          // 1. Find or Create User
          let user = await prisma.user.findUnique({ 
            where: { email },
            include: { memberships: true } 
          });
          
          if (!user) {
            console.log("Creating new user for:", email);
            user = await prisma.user.create({
              data: {
                email,
                name: email.split('@')[0],
                image: `https://api.dicebear.com/7.x/avataaars/svg?seed=${email}`,
              }
            });
            
            // 2. Create Default Org for new user
            const slug = user.name
              .toLowerCase()
              .replace(/[^a-z0-9]+/g, '-')
              .replace(/^-|-$/g, '')
              + '-' + user.id.slice(0, 6);

            const org = await prisma.organization.create({
              data: {
                name: `${user.name}'s Workspace`,
                slug,
                members: {
                  create: {
                    userId: user.id,
                    role: 'OWNER',
                  },
                },
              },
            });
            
            // Attach orgId manually since we just created it
            user.orgId = org.id;
          } else {
             console.log("Found existing user:", email);
             
             if (user.memberships && user.memberships.length > 0) {
                // FIX: Use 'orgId' instead of 'organizationId' to match schema
                user.orgId = user.memberships[0].orgId;
             } else {
                console.log("User has no orgs. Creating default org...");
                // Create Default Org for orphaned user
                const slug = user.name
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, '-')
                  .replace(/^-|-$/g, '')
                  + '-' + user.id.slice(0, 6);

                const org = await prisma.organization.create({
                  data: {
                    name: `${user.name}'s Workspace`,
                    slug,
                    members: {
                      create: {
                        userId: user.id,
                        role: 'OWNER',
                      },
                    },
                  },
                });
                user.orgId = org.id;
             }
          }
          
          // Return a sanitized object to avoid serialization issues
          return {
            id: user.id,
            name: user.name,
            email: user.email,
            image: user.image,
            orgId: user.orgId,
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
    async jwt({ token, user, trigger, session }) {
      // 1. Initial Sign-In
      if (user) {
        token.id = user.id;
        token.orgId = user.orgId;
      }

      // 2. Client-side Update
      if (trigger === 'update' && session?.orgId) {
        token.orgId = session.orgId;
      }

      return token;
    },

    async session({ session, token }) {
      if (token) {
        session.user.id = token.id;
        session.user.orgId = token.orgId;
      }
      return session;
    },
  },

  // ── Events ───────────────────────────────────
  events: {
    /**
     * When a new user signs up via OAuth, automatically
     * create a personal Organization for them.
     */
    async createUser({ user }) {
      if (!user.id || !user.name) return;

      const slug = user.name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '')
        + '-' + user.id.slice(0, 6);

      await prisma.organization.create({
        data: {
          name: `${user.name}'s Workspace`,
          slug,
          members: {
            create: {
              userId: user.id,
              role: 'OWNER',
            },
          },
        },
      });
    },
  },
});
