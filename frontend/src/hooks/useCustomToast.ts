import { useTranslation } from "react-i18next"
import { toast } from "sonner"

const useCustomToast = () => {
  const { t } = useTranslation()

  const showSuccessToast = (description: string) => {
    toast.success(t("common.success"), {
      description,
    })
  }

  const showErrorToast = (description: string) => {
    toast.error(t("common.error"), {
      description,
    })
  }

  const showInfoToast = (description: string) => {
    toast.info(t("common.info"), {
      description,
    })
  }

  return { showSuccessToast, showErrorToast, showInfoToast }
}

export default useCustomToast
