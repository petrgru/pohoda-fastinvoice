import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus, Trash2 } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import {
  type SupplierKnownAccount,
  type SupplierPublic,
  SuppliersService,
} from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

type AccountRow = { account: string; iban: string; confirmed: boolean }

export function ManageSupplier({ supplier }: { supplier: SupplierPublic }) {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const [name, setName] = useState(supplier.name)
  const [dic, setDic] = useState(supplier.dic ?? "")
  const [accounts, setAccounts] = useState<AccountRow[]>(
    (supplier.bank_accounts ?? []).map((row) => ({
      account: row.account ?? "",
      iban: row.iban ?? "",
      confirmed: row.confirmed ?? false,
    })),
  )
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const invoices = useQuery({
    queryKey: ["supplier-invoices", supplier.id],
    queryFn: async () =>
      (
        await SuppliersService.supplierInvoices({
          path: { supplier_id: supplier.id },
          query: { limit: 20 },
        })
      ).data,
    enabled: isOpen,
  })

  const mutation = useMutation({
    mutationFn: () =>
      SuppliersService.updateSupplier({
        path: { supplier_id: supplier.id },
        body: {
          name,
          dic: dic || null,
          bank_accounts: accounts
            .filter((row) => row.account || row.iban)
            .map((row) => ({
              account: row.account || null,
              iban: row.iban || null,
              confirmed: row.confirmed,
            })) satisfies SupplierKnownAccount[],
        },
      }),
    onSuccess: () => {
      showSuccessToast(t("suppliers.saved"))
      queryClient.invalidateQueries({ queryKey: ["suppliers"] })
      setIsOpen(false)
    },
    onError: handleError.bind(showErrorToast),
  })

  const setRow = (index: number, patch: Partial<AccountRow>) =>
    setAccounts((rows) =>
      rows.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    )

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        data-testid="manage-supplier-button"
        onClick={() => {
          setName(supplier.name)
          setDic(supplier.dic ?? "")
          setAccounts(
            (supplier.bank_accounts ?? []).map((row) => ({
              account: row.account ?? "",
              iban: row.iban ?? "",
              confirmed: row.confirmed ?? false,
            })),
          )
          setIsOpen(true)
        }}
      >
        {t("suppliers.manage")}
      </Button>
      <Dialog open={isOpen} onOpenChange={setIsOpen}>
        <DialogContent
          className="max-h-[90vh] max-w-2xl overflow-y-auto"
          data-testid="manage-supplier-dialog"
        >
          <DialogHeader>
            <DialogTitle>{t("suppliers.manageTitle")}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="supplier-name">{t("suppliers.name")}</Label>
              <Input
                id="supplier-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="supplier-dic">{t("suppliers.dic")}</Label>
              <Input
                id="supplier-dic"
                value={dic}
                placeholder="CZ47123737"
                onChange={(e) => setDic(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label>{t("suppliers.accounts")}</Label>
              <p className="text-xs text-muted-foreground">
                {t("suppliers.accountsHint")}
              </p>
              {accounts.map((row, index) => (
                <div
                  key={index}
                  className="grid grid-cols-[1fr_1fr_auto_auto] items-center gap-2"
                  data-testid={`supplier-account-row-${index}`}
                >
                  <Input
                    placeholder={t("suppliers.accountPlaceholder")}
                    value={row.account}
                    onChange={(e) => setRow(index, { account: e.target.value })}
                  />
                  <Input
                    placeholder="CZ88 0300 0000 0000 0091"
                    value={row.iban}
                    onChange={(e) => setRow(index, { iban: e.target.value })}
                  />
                  <label
                    className="flex items-center gap-1 text-xs"
                    htmlFor={`supplier-account-confirmed-${index}`}
                  >
                    <Checkbox
                      id={`supplier-account-confirmed-${index}`}
                      data-testid={`supplier-account-confirmed-${index}`}
                      checked={row.confirmed}
                      onCheckedChange={(checked) =>
                        setRow(index, { confirmed: checked === true })
                      }
                    />
                    {t("suppliers.confirmed")}
                  </label>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("suppliers.removeAccount")}
                    onClick={() =>
                      setAccounts((rows) => rows.filter((_, i) => i !== index))
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() =>
                  setAccounts((rows) => [
                    ...rows,
                    { account: "", iban: "", confirmed: false },
                  ])
                }
              >
                <Plus className="mr-1 size-4" />
                {t("suppliers.addAccount")}
              </Button>
            </div>
            <div className="grid gap-2">
              <Label>{t("suppliers.invoices")}</Label>
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("suppliers.docNumber")}</TableHead>
                      <TableHead>{t("suppliers.total")}</TableHead>
                      <TableHead>{t("suppliers.createdAt")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(invoices.data?.data ?? []).map((invoice) => (
                      <TableRow key={invoice.id}>
                        <TableCell>
                          <a
                            className="text-primary underline-offset-4 hover:underline"
                            href={`/invoices/${invoice.id}`}
                          >
                            {invoice.invoice_number ??
                              invoice.original_filename}
                          </a>
                        </TableCell>
                        <TableCell>{invoice.total_price ?? "–"}</TableCell>
                        <TableCell>
                          {new Date(
                            invoice.created_at ?? "",
                          ).toLocaleDateString()}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={mutation.isPending}>
                {t("common.cancel")}
              </Button>
            </DialogClose>
            <LoadingButton
              loading={mutation.isPending}
              data-testid="manage-supplier-save"
              onClick={() => mutation.mutate()}
            >
              {t("common.save")}
            </LoadingButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
