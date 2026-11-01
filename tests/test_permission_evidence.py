from __future__ import annotations

import unittest

from usit.permission_evidence import extract_authorization_evidence


class AuthorizationEvidenceTests(unittest.TestCase):
    def test_extracts_route_middleware_and_named_permission_calls(self):
        rows = extract_authorization_evidence({
            "routes/photos.ts": (
                "router.use(authRequired, adminRequired);\n"
                "router.get('/photos', authRequired, listPhotos);\n"
            ),
            "services/photo_service.ts": (
                "export async function listPhotos(user) {\n"
                "  await requirePermission(user, 'photo.read');\n"
                "  return repo.list();\n"
                "}\n"
            ),
        })
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["kind"], "route_middleware")
        self.assertEqual(rows[0]["guard_names"], ["adminRequired", "authRequired"])
        self.assertEqual(rows[1]["kind"], "route_middleware")
        self.assertEqual(rows[2]["kind"], "authorization_call")
        self.assertEqual(rows[2]["guard_names"], ["requirePermission"])
        self.assertEqual(rows[2]["verification_status"], "NOT_VERIFIED")

    def test_extracts_role_and_owner_conditions_as_candidates(self):
        rows = extract_authorization_evidence({
            "services/photo_service.ts": (
                "if (req.user.role === 'admin') { allow(); }\n"
                "if (photo.owner_id === req.user.id) { show(); }\n"
            )
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["role_value"], "admin")
        self.assertTrue(rows[1]["owner_condition"])
        self.assertEqual(rows[1]["kind"], "authorization_condition")


if __name__ == "__main__":
    unittest.main()
