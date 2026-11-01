import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { Loader2, Plus, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import {
  type Control,
  useFieldArray,
  useForm,
  useFormContext,
  useWatch,
} from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import { CompaniesService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

/** Loose client-side check mirroring the backend validator (mod-11 is NOT
 * required for accounts – real Czech bank accounts legitimately fail it). */
export const BANK_ACCOUNT_FORMAT =
  /^(?:\d{1,6}[-/])?\d{1,16}(?:\/\d{4})?$|^[A-Z]{2}\d{2}[A-Z0-9 ]{10,34}$/i

/** DIČ format only – the real validity comes from the VatComply/VIES lookup. */
export const DIC_FORMAT = /^\d{8,10}$|^[A-Z]{2}[A-Z0-9]{8,12}$/i

export const IBAN_FORMAT = /^[A-Z]{2}\d{2}[A-Z0-9]{10,32}$/i

/** IBANs are commonly written in 4-char groups – ignore inner spaces. */
const ibanValid = (value: string) => IBAN_FORMAT.test(value.replace(/\s+/g, ""))

export const bankAccountRowSchema = z.object({
  account: z.string(),
  iban: z.string(),
})

export const companySchema = z.object({
  name: z.string(),
  ico: z.string(),
  dic: z.string().optional(),
  address: z.string().max(255).optional(),
  bank_account: z.string(),
  iban: z.string().optional(),
  other_accounts: z.array(bankAccountRowSchema).optional(),
  note: z.string().optional(),
})

export type CompanyFormData = z.infer<typeof companySchema>

/** Localized schema – built per-render so messages follow the active language. */
export const createCompanySchema = (t: (key: string) => string) =>
  companySchema.extend({
    name: z.string().min(1, t("companyFields.errName")),
    ico: z.string().regex(/^\d{8}$/, t("companyFields.errIco")),
    dic: z
      .string()
      .optional()
      .refine(
        (value) => !value || DIC_FORMAT.test(value.trim()),
        t("companyFields.errDicFormat"),
      ),
    bank_account: z
      .string()
      .min(1, t("companyFields.errAccount"))
      .refine(
        (value) => BANK_ACCOUNT_FORMAT.test(value.trim()),
        t("companyFields.errAccountFormat"),
      ),
    iban: z
      .string()
      .optional()
      .refine(
        (value) => !value || ibanValid(value),
        t("companyFields.errIbanFormat"),
      ),
    other_accounts: z
      .array(
        z.object({
          account: z
            .string()
            .refine(
              (value) =>
                !value.trim() || BANK_ACCOUNT_FORMAT.test(value.trim()),
              t("companyFields.errAccountFormat"),
            ),
          iban: z
            .string()
            .refine(
              (value) => !value.trim() || ibanValid(value),
              t("companyFields.errIbanFormat"),
            ),
        }),
      )
      .optional(),
  })

/** Maps form values to the company payload – the first row doubles as the
 * legacy primary `bank_account` used by the Pohoda export. */
export const toBankAccountRows = (data: {
  bank_account?: string
  iban?: string
  other_accounts?: { account: string; iban: string }[]
}) => [
  {
    account: data.bank_account?.trim() || null,
    iban: data.iban?.trim() || null,
  },
  ...(data.other_accounts ?? [])
    .filter((row) => row.account.trim() || row.iban.trim())
    .map((row) => ({
      account: row.account.trim() || null,
      iban: row.iban.trim() || null,
    })),
]

/** Splits stored company rows back into the form fields. */
export const fromBankAccountRows = (
  rows?: { account?: string | null; iban?: string | null }[] | null,
  fallbackAccount?: string | null,
) => {
  const list = rows ?? []
  return {
    bank_account: list[0]?.account ?? fallbackAccount ?? "",
    iban: list[0]?.iban ?? "",
    other_accounts: list.slice(1).map((row) => ({
      account: row.account ?? "",
      iban: row.iban ?? "",
    })),
  }
}

/**
 * Extra account rows parsed from an invoice for the add-company dialog – the
 * row already covered by the primary account/IBAN inputs is dropped so it is
 * not displayed (and later stored) twice.
 */
export const invoiceExtraAccounts = (invoice: {
  supplier_bank_accounts?:
    | { account?: string | null; iban?: string | null }[]
    | null
  supplier_bank_account?: string | null
  supplier_iban?: string | null
}) => {
  const rows = (invoice.supplier_bank_accounts ?? []).filter(
    (row) => row.account || row.iban,
  )
  const primary = rows.findIndex(
    (row) =>
      (row.account ?? "") === (invoice.supplier_bank_account ?? "") &&
      (row.iban ?? "") === (invoice.supplier_iban ?? ""),
  )
  if (primary !== -1) {
    rows.splice(primary, 1)
  }
  return rows.map((row) => ({
    account: row.account ?? "",
    iban: row.iban ?? "",
  }))
}

type VatState = "idle" | "checking" | "invalid" | "unavailable"

/**
 * Shared company form fields. When a DIČ is entered (or prefilled with
 * `autoLookup`), the registered name and address are fetched from the VAT
 * registry (VatComply/VIES): a confirmed-invalid DIČ blocks the submit,
 * an unreachable registry only warns so saving stays possible.
 */
export const CompanyFields = ({
  control: rawControl,
  disabled,
  autoLookup = false,
}: {
  // akceptuje i rozšířené schéma (EditCompany přidává is_active)
  control:
    | Control<CompanyFormData>
    | Control<CompanyFormData & { is_active: boolean }>
  disabled?: boolean
  /** Look the prefilled DIČ up in the registry as soon as the fields mount. */
  autoLookup?: boolean
}) => {
  const { t } = useTranslation()
  const control = rawControl as Control<CompanyFormData>
  // shares the parent form state – we need setValue/setError for the autofill
  const { setValue, setError, clearErrors } = useFormContext<CompanyFormData>()
  const {
    fields: extraAccounts,
    append: appendAccount,
    remove: removeAccount,
  } = useFieldArray({ control, name: "other_accounts" })
  const dic = (useWatch({ control, name: "dic" }) ?? "").trim()
  const normalized = dic.replace(/\s/g, "").toUpperCase()
  const [vatState, setVatState] = useState<VatState>("idle")
  const lastChecked = useRef("")

  const checkVat = async () => {
    if (!normalized || normalized === lastChecked.current) {
      return
    }
    if (!DIC_FORMAT.test(normalized)) {
      return // zod reports the format error; no registry call
    }
    lastChecked.current = normalized
    setVatState("checking")
    try {
      const { data } = await CompaniesService.getVatInfo({
        query: { dic: normalized },
      })
      if (data.valid) {
        setVatState("idle")
        clearErrors("dic")
        if (data.name) {
          setValue("name", data.name, { shouldDirty: true })
        }
        if (data.address) {
          setValue("address", data.address, { shouldDirty: true })
        }
      } else {
        setVatState("invalid")
        setError("dic", { type: "vat", message: t("companyFields.vatInvalid") })
      }
    } catch (error) {
      if (error instanceof AxiosError && error.response?.status === 502) {
        // registry unreachable -> warn, do not block saving (backend fails open)
        setVatState("unavailable")
      } else {
        setVatState("idle")
      }
    }
  }

  // editing the DIČ invalidates any previous registry verdict
  useEffect(() => {
    if (lastChecked.current !== normalized) {
      lastChecked.current = ""
      setVatState("idle")
      const error = control.getFieldState("dic").error
      if (error?.type === "vat") {
        clearErrors("dic")
      }
    }
  }, [normalized, control, clearErrors])

  // dialogs with a prefilled DIČ (created from an invoice) check it right away
  // biome-ignore lint/correctness/useExhaustiveDependencies: run once on dialog mount
  useEffect(() => {
    if (autoLookup) {
      void checkVat()
    }
  }, [])

  return (
    <div className="grid gap-4 py-4">
      <FormField
        control={control}
        name="name"
        render={({ field }) => (
          <FormItem>
            <FormLabel>
              {t("companyFields.name")}{" "}
              <span className="text-destructive">*</span>
            </FormLabel>
            <FormControl>
              <Input
                placeholder={t("companyFields.namePlaceholder")}
                {...field}
                disabled={disabled}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="ico"
        render={({ field }) => (
          <FormItem>
            <FormLabel>
              {t("companyFields.ico")}{" "}
              <span className="text-destructive">*</span>
            </FormLabel>
            <FormControl>
              <Input
                placeholder={t("companyFields.icoPlaceholder")}
                {...field}
                disabled={disabled}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="dic"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("companyFields.dic")}</FormLabel>
            <FormControl>
              <Input
                placeholder="CZ47123737"
                {...field}
                disabled={disabled}
                onBlur={() => {
                  field.onBlur()
                  void checkVat()
                }}
              />
            </FormControl>
            {vatState === "checking" && (
              <p
                className="flex items-center gap-1 text-xs text-muted-foreground"
                data-testid="vat-status"
              >
                <Loader2 className="size-3 animate-spin" />
                {t("companyFields.vatChecking")}
              </p>
            )}
            {vatState === "invalid" && (
              <p className="text-xs text-destructive" data-testid="vat-status">
                {t("companyFields.vatInvalid")}
              </p>
            )}
            {vatState === "unavailable" && (
              <p className="text-xs text-amber-600" data-testid="vat-status">
                {t("companyFields.vatUnavailable")}
              </p>
            )}
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={control}
        name="address"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("companyFields.address")}</FormLabel>
            <FormControl>
              <Input
                placeholder={t("companyFields.addressPlaceholder")}
                {...field}
                disabled={disabled}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <div className="grid gap-3 rounded-md border p-3">
        <div className="grid grid-cols-2 gap-3">
          <FormField
            control={control}
            name="bank_account"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {t("companyFields.account")}{" "}
                  <span className="text-destructive">*</span>
                </FormLabel>
                <FormControl>
                  <Input
                    placeholder={t("companyFields.accountPlaceholder")}
                    {...field}
                    disabled={disabled}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="iban"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("companyFields.iban")}</FormLabel>
                <FormControl>
                  <Input
                    placeholder={t("companyFields.ibanPlaceholder")}
                    {...field}
                    disabled={disabled}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        {extraAccounts.map((row, index) => (
          <div
            key={row.id}
            className="grid grid-cols-[1fr_1fr_auto] items-start gap-3"
            data-testid="extra-account-row"
          >
            <FormField
              control={control}
              name={`other_accounts.${index}.account`}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("companyFields.account")}</FormLabel>
                  <FormControl>
                    <Input
                      placeholder={t("companyFields.accountPlaceholder")}
                      {...field}
                      disabled={disabled}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={control}
              name={`other_accounts.${index}.iban`}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("companyFields.iban")}</FormLabel>
                  <FormControl>
                    <Input
                      placeholder={t("companyFields.ibanPlaceholder")}
                      {...field}
                      disabled={disabled}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="mt-7"
              disabled={disabled}
              onClick={() => removeAccount(index)}
              aria-label={t("companyFields.removeAccount")}
              data-testid="remove-account-button"
            >
              <X className="size-4" />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="justify-self-start"
          disabled={disabled}
          onClick={() => appendAccount({ account: "", iban: "" })}
          data-testid="add-bank-account-button"
        >
          <Plus className="mr-2 size-4" />
          {t("companyFields.addAccount")}
        </Button>
      </div>
      <FormField
        control={control}
        name="note"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("companyFields.note")}</FormLabel>
            <FormControl>
              <Input {...field} disabled={disabled} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  )
}

export const AddCompany = () => {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<CompanyFormData>({
    resolver: zodResolver(createCompanySchema(t)),
    mode: "onBlur",
    defaultValues: {
      name: "",
      ico: "",
      dic: "",
      address: "",
      bank_account: "",
      iban: "",
      other_accounts: [],
      note: "",
    },
  })

  const mutation = useMutation({
    mutationFn: (data: CompanyFormData) =>
      CompaniesService.createCompany({
        body: {
          name: data.name,
          ico: data.ico,
          dic: data.dic || null,
          address: data.address || null,
          bank_account: data.bank_account,
          bank_accounts: toBankAccountRows(data),
          note: data.note || null,
        } as never,
      }),
    onSuccess: () => {
      showSuccessToast(t("companies.created"))
      setIsOpen(false)
      form.reset()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] })
    },
  })

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <Button onClick={() => setIsOpen(true)} data-testid="add-company-button">
        <Plus className="mr-2 size-4" />
        {t("companies.add")}
      </Button>
      <DialogContent className="max-h-[90vh] max-w-md overflow-y-auto">
        <Form {...form}>
          <form onSubmit={form.handleSubmit((data) => mutation.mutate(data))}>
            <DialogHeader>
              <DialogTitle>{t("companies.addTitle")}</DialogTitle>
              <DialogDescription>{t("companies.addHint")}</DialogDescription>
            </DialogHeader>
            <CompanyFields control={form.control} />
            <DialogFooter>
              <DialogClose asChild>
                <Button
                  variant="outline"
                  type="button"
                  disabled={mutation.isPending}
                >
                  {t("common.cancel")}
                </Button>
              </DialogClose>
              <LoadingButton
                type="submit"
                loading={mutation.isPending}
                data-testid="add-company-confirm-button"
              >
                {t("common.create")}
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
