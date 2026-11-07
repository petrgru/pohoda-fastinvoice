import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import {
  CompaniesService,
  type MembershipPublic,
  type RecipientApiKeyPublic,
  type RecipientCompanyPublic,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

function formatDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleDateString() : "—"
}

function MemberRow({
  member,
  canManage,
  companyId,
}: {
  member: MembershipPublic
  canManage: boolean
  companyId: string
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const changeRole = useMutation({
    mutationFn: (role: string) =>
      CompaniesService.updateMemberRole({
        body: { role: role as "admin" | "editor" },
        path: { recipient_company_id: companyId, membership_id: member.id },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["company-members"] })
      showSuccessToast(t("manage.saved"))
    },
    onError: handleError.bind(showErrorToast),
  })

  const remove = useMutation({
    mutationFn: () =>
      CompaniesService.removeMember({
        path: { recipient_company_id: companyId, membership_id: member.id },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["company-members"] })
      showSuccessToast(t("manage.memberRemoved"))
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <div
      className="flex items-center gap-2"
      data-testid={`member-row-${member.id}`}
    >
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm">{member.email}</span>
        {!member.is_active && (
          <span className="text-xs text-muted-foreground">
            {t("manage.inactive")}
          </span>
        )}
      </div>
      {canManage ? (
        <Select
          value={member.role}
          disabled={changeRole.isPending}
          onValueChange={(role) => changeRole.mutate(role)}
        >
          <SelectTrigger className="w-36" aria-label={t("manage.role")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="admin">{t("manage.roleAdmin")}</SelectItem>
            <SelectItem value="editor">{t("manage.roleEditor")}</SelectItem>
          </SelectContent>
        </Select>
      ) : (
        <Badge variant="outline">
          {member.role === "admin"
            ? t("manage.roleAdmin")
            : t("manage.roleEditor")}
        </Badge>
      )}
      {canManage && (
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground"
          onClick={() => remove.mutate()}
          data-testid={`member-remove-${member.id}`}
        >
          ✕
        </Button>
      )}
    </div>
  )
}

function ApiKeyRow({
  keyItem,
  companyId,
}: {
  keyItem: RecipientApiKeyPublic
  companyId: string
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const revoke = useMutation({
    mutationFn: () =>
      CompaniesService.revokeApiKey({
        path: { recipient_company_id: companyId, key_id: keyItem.id },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["company-api-keys"] })
      showSuccessToast(t("manage.keyRevoked"))
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <div
      className="flex items-center gap-2"
      data-testid={`api-key-row-${keyItem.id}`}
    >
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium">{keyItem.label}</span>
        <span className="font-mono text-xs text-muted-foreground">
          {keyItem.key_prefix}…
        </span>
      </div>
      <span className="text-xs text-muted-foreground">
        {keyItem.last_used_at
          ? `${t("manage.lastUsed")}: ${formatDate(keyItem.last_used_at)}`
          : t("manage.neverUsed")}
      </span>
      {keyItem.revoked_at ? (
        <Badge variant="outline" className="text-muted-foreground">
          {t("manage.revoked")}
        </Badge>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          className="text-destructive"
          onClick={() => revoke.mutate()}
          data-testid={`api-key-revoke-${keyItem.id}`}
        >
          {t("manage.revoke")}
        </Button>
      )}
    </div>
  )
}

export function ManageCompany({
  company,
}: {
  company: RecipientCompanyPublic
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<"admin" | "editor">("editor")
  const [label, setLabel] = useState("")
  const [newKey, setNewKey] = useState<string | null>(null)

  const canManage = company.my_role === "admin"

  const members = useQuery({
    queryKey: ["company-members", company.id],
    queryFn: async () =>
      (
        await CompaniesService.listMembers({
          path: { recipient_company_id: company.id },
        })
      ).data,
    enabled: open,
  })

  const keys = useQuery({
    queryKey: ["company-api-keys", company.id],
    queryFn: async () =>
      (
        await CompaniesService.listApiKeys({
          path: { recipient_company_id: company.id },
        })
      ).data,
    enabled: open,
  })

  const addMember = useMutation({
    mutationFn: () =>
      CompaniesService.addMember({
        body: { email, role },
        path: { recipient_company_id: company.id },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["company-members"] })
      setEmail("")
      showSuccessToast(t("manage.memberAdded"))
    },
    onError: handleError.bind(showErrorToast),
  })

  const createKey = useMutation({
    mutationFn: () =>
      CompaniesService.createApiKey({
        body: { label },
        path: { recipient_company_id: company.id },
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["company-api-keys"] })
      setNewKey(created.data.key)
      setLabel("")
    },
    onError: handleError.bind(showErrorToast),
  })

  const copyKey = async () => {
    if (!newKey) return
    await navigator.clipboard.writeText(newKey)
    showSuccessToast(t("manage.copied"))
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          className="flex w-full items-center rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-accent"
          data-testid={`company-manage-${company.id}`}
        >
          {t("manage.title")}
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t("manage.heading", { name: company.name })}
          </DialogTitle>
        </DialogHeader>

        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">{t("manage.members")}</h3>
          {(members.data ?? []).map((member) => (
            <MemberRow
              key={member.id}
              member={member}
              canManage={canManage}
              companyId={company.id}
            />
          ))}
          {canManage && (
            <form
              className="flex flex-wrap items-center gap-2 pt-1"
              onSubmit={(event) => {
                event.preventDefault()
                if (email.trim()) addMember.mutate()
              }}
            >
              <Input
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder={t("manage.emailPlaceholder")}
                className="min-w-40 flex-1"
                data-testid="manage-member-email"
              />
              <Select
                value={role}
                onValueChange={(value) => setRole(value as "admin" | "editor")}
              >
                <SelectTrigger className="w-36" aria-label={t("manage.role")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">{t("manage.roleAdmin")}</SelectItem>
                  <SelectItem value="editor">
                    {t("manage.roleEditor")}
                  </SelectItem>
                </SelectContent>
              </Select>
              <Button
                type="submit"
                size="sm"
                disabled={addMember.isPending}
                data-testid="manage-member-add"
              >
                {t("manage.addMember")}
              </Button>
            </form>
          )}
        </section>

        {canManage && (
          <section className="flex flex-col gap-2 border-t pt-3">
            <h3 className="text-sm font-semibold">{t("manage.keys")}</h3>
            <p className="text-xs text-muted-foreground">
              {t("manage.keysHint")}
            </p>
            {(keys.data ?? []).map((keyItem) => (
              <ApiKeyRow
                key={keyItem.id}
                keyItem={keyItem}
                companyId={company.id}
              />
            ))}
            {newKey && (
              <div
                className="flex items-center gap-2 rounded-md border border-dashed p-2"
                data-testid="manage-new-key"
              >
                <code className="min-w-0 flex-1 truncate font-mono text-xs">
                  {newKey}
                </code>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={copyKey}
                >
                  {t("manage.copy")}
                </Button>
              </div>
            )}
            <form
              className="flex items-center gap-2 pt-1"
              onSubmit={(event) => {
                event.preventDefault()
                if (label.trim()) createKey.mutate()
              }}
            >
              <Input
                required
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                placeholder={t("manage.keyLabelPlaceholder")}
                className="min-w-40 flex-1"
                data-testid="manage-key-label"
              />
              <Button
                type="submit"
                size="sm"
                disabled={createKey.isPending}
                data-testid="manage-key-create"
              >
                {t("manage.createKey")}
              </Button>
            </form>
          </section>
        )}
      </DialogContent>
    </Dialog>
  )
}
