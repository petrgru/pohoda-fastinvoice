import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Camera, ScanLine, Upload, X } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { CompaniesService, InvoicesService } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useActiveCompany } from "@/hooks/useActiveCompany"
import useCustomToast from "@/hooks/useCustomToast"
import { findContentRect } from "@/lib/autotrim"
import {
  discoverScanners,
  type ScannerProfile,
  scanWithProfile,
} from "@/lib/escl"
import { cn } from "@/lib/utils"
import { handleError } from "@/utils"

// A4 at 150 dpi – the scanner output size
const SCAN_WIDTH = 1240
const SCAN_HEIGHT = 1754

// remembered preference, on by default
const AUTO_TRIM_KEY = "invoice-auto-trim"

type CaptureMode = "photo" | "scan"

const UploadInvoice = () => {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [companyId, setCompanyId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast, showInfoToast } = useCustomToast()

  // the camera is offered only when the browser can actually reach one
  const [cameraAvailable, setCameraAvailable] = useState(false)
  const [captureMode, setCaptureMode] = useState<CaptureMode | null>(null)
  const [cameraReady, setCameraReady] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const captureInputRef = useRef<HTMLInputElement>(null)
  // shared NAPS2 profiles found on the localhost eSCL ports (null = unprobed)
  const [naps2Profiles, setNaps2Profiles] = useState<ScannerProfile[] | null>(
    null,
  )
  const [scannerPort, setScannerPort] = useState<string | null>(null)
  const [scanning, setScanning] = useState(false)
  // trims the white margins of scans and camera captures, on by default
  const [autoTrim, setAutoTrim] = useState(
    () => localStorage.getItem(AUTO_TRIM_KEY) !== "false",
  )
  // preview of the picked or captured file, shown before the upload
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const previewUrlRef = useRef<string | null>(null)

  useEffect(() => {
    if (!navigator.mediaDevices?.getUserMedia) return
    const probe = async () => {
      const devices = await navigator.mediaDevices
        .enumerateDevices()
        .then((list) => list.some((device) => device.kind === "videoinput"))
        .catch(() => false)
      if (devices) {
        setCameraAvailable(true)
        return
      }
      // nothing enumerable before a permission grant – ask the permission API
      const status = await navigator.permissions
        ?.query({ name: "camera" as PermissionName })
        .catch(() => null)
      if (status) setCameraAvailable(status.state === "granted")
    }
    probe()
  }, [])

  const stopCamera = useCallback(() => {
    const stream = streamRef.current
    streamRef.current = null
    stream?.getTracks().forEach((track) => {
      track.onended = null
      track.stop()
    })
    setCaptureMode(null)
    setCameraReady(false)
  }, [])

  // the OS, a privacy switch or a backgrounded tab may end the stream –
  // fall back to the idle UI instead of showing a black screen
  const handleStreamEnded = useCallback(() => {
    if (!streamRef.current) return
    stopCamera()
    showInfoToast(t("upload.cameraEnded"))
  }, [stopCamera, showInfoToast, t])

  // sets the file to upload and its preview; `preview` carries the first
  // page of a scan as an image, images preview themselves, PDFs have none
  const applyFile = useCallback((next: File | null, preview?: Blob) => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
    const source = preview ?? (next?.type.startsWith("image/") ? next : null)
    previewUrlRef.current = source ? URL.createObjectURL(source) : null
    setPreviewUrl(previewUrlRef.current)
    setFile(next)
  }, [])

  useEffect(
    () => () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
    },
    [],
  )

  const toggleAutoTrim = useCallback((checked: boolean) => {
    setAutoTrim(checked)
    localStorage.setItem(AUTO_TRIM_KEY, String(checked))
  }, [])

  const startCamera = useCallback(
    async (mode: CaptureMode) => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video:
            mode === "scan"
              ? {
                  width: { ideal: SCAN_WIDTH },
                  height: { ideal: SCAN_HEIGHT },
                  facingMode: "environment",
                }
              : { facingMode: "environment" },
        })
        stream.getTracks().forEach((track) => {
          track.onended = handleStreamEnded
        })
        streamRef.current = stream
        setCameraReady(false)
        setCaptureMode(mode)
        applyFile(null)
        return true
      } catch {
        // permission refused or no real camera – fall back to the native
        // camera picker where the platform offers one
        showErrorToast(t("upload.cameraDenied"))
        captureInputRef.current?.click()
        return false
      }
    },
    [applyFile, handleStreamEnded, showErrorToast, t],
  )

  // "Scan" prefers a real shared NAPS2 scanner, camera scanning is the fallback
  const handleScanClick = async () => {
    setScanning(true)
    const profiles = await discoverScanners()
    setScanning(false)
    if (profiles.length > 0) {
      setNaps2Profiles(profiles)
      setScannerPort(String(profiles[0].port))
      applyFile(null)
      return
    }
    const started = await startCamera("scan")
    if (started) showInfoToast(t("upload.naps2Hint"))
  }

  const runNaps2Scan = async () => {
    if (!scannerPort) return
    setScanning(true)
    try {
      const result = await scanWithProfile(Number(scannerPort), { autoTrim })
      applyFile(result.file, result.preview)
    } catch (error) {
      showErrorToast(
        `${t("upload.scannerError")} (${String(error).slice(0, 120)})`,
      )
    } finally {
      setScanning(false)
    }
  }

  const grabFrame = useCallback(
    (mode: CaptureMode, trim: boolean): File | null => {
      const video = videoRef.current
      if (!video || !streamRef.current) return null
      let canvas = document.createElement("canvas")
      canvas.width = mode === "scan" ? SCAN_WIDTH : video.videoWidth || 640
      canvas.height = mode === "scan" ? SCAN_HEIGHT : video.videoHeight || 480
      const context = canvas.getContext("2d")
      if (!context) return null
      try {
        if (mode === "scan") {
          // white A4 sheet with the frame scaled to fit – then black & white
          context.fillStyle = "#ffffff"
          context.fillRect(0, 0, canvas.width, canvas.height)
          const scale = Math.min(
            canvas.width / (video.videoWidth || canvas.width),
            canvas.height / (video.videoHeight || canvas.height),
          )
          const width = (video.videoWidth || canvas.width) * scale
          const height = (video.videoHeight || canvas.height) * scale
          context.drawImage(
            video,
            (canvas.width - width) / 2,
            (canvas.height - height) / 2,
            width,
            height,
          )
          const image = context.getImageData(0, 0, canvas.width, canvas.height)
          for (let i = 0; i < image.data.length; i += 4) {
            const gray =
              image.data[i] * 0.299 +
              image.data[i + 1] * 0.587 +
              image.data[i + 2] * 0.114
            const blackWhite = gray < 150 ? 0 : 255
            image.data[i] = blackWhite
            image.data[i + 1] = blackWhite
            image.data[i + 2] = blackWhite
          }
          const rect = trim ? findContentRect([image]) : null
          if (rect) {
            // keep only the content with a margin around it
            const cropped = document.createElement("canvas")
            cropped.width = rect.width
            cropped.height = rect.height
            context.putImageData(image, -rect.x, -rect.y)
            const croppedContext = cropped.getContext("2d")
            if (!croppedContext) return null
            croppedContext.drawImage(
              canvas,
              rect.x,
              rect.y,
              rect.width,
              rect.height,
              0,
              0,
              rect.width,
              rect.height,
            )
            canvas = cropped
          } else {
            context.putImageData(image, 0, 0)
          }
        } else {
          context.drawImage(video, 0, 0, canvas.width, canvas.height)
        }
      } catch {
        // frame not painted yet – the button is gated, this is just insurance
        return null
      }
      const blob = canvas.toDataURL("image/jpeg", 0.92)
      const bytes = atob(blob.split(",")[1] ?? "")
      const buffer = new Uint8Array(bytes.length)
      for (let i = 0; i < bytes.length; i++) buffer[i] = bytes.charCodeAt(i)
      const name =
        mode === "scan" ? `sken-${Date.now()}.jpg` : `doklad-${Date.now()}.jpg`
      return new File([buffer], name, { type: "image/jpeg" })
    },
    [],
  )

  const capturePhoto = useCallback(
    (mode: CaptureMode, trim: boolean) => {
      const captured = grabFrame(mode, trim)
      stopCamera()
      if (captured) applyFile(captured)
    },
    [applyFile, grabFrame, stopCamera],
  )

  // keep the preview alive: browsers pause background videos, phones may
  // end the stream entirely – and some stacks never fire "ended" on the
  // track, so also poll its readiness while the preview is open
  useEffect(() => {
    if (!captureMode) return
    const poll = setInterval(() => {
      const tracks = streamRef.current?.getTracks() ?? []
      if (tracks.every((track) => track.readyState === "ended")) {
        handleStreamEnded()
      }
    }, 1000)
    const onVisibility = () => {
      if (document.visibilityState !== "visible") return
      const tracks = streamRef.current?.getTracks() ?? []
      if (tracks.length === 0 || tracks.every((track) => !track.readyState)) {
        handleStreamEnded()
        return
      }
      videoRef.current?.play().catch(() => undefined)
    }
    document.addEventListener("visibilitychange", onVisibility)
    return () => {
      clearInterval(poll)
      document.removeEventListener("visibilitychange", onVisibility)
    }
  }, [captureMode, handleStreamEnded])

  // never leave the camera running when the dialog goes away
  useEffect(() => {
    if (!isOpen) stopCamera()
  }, [isOpen, stopCamera])
  useEffect(() => stopCamera, [stopCamera])

  // the company picked in the header pre-selects the recipient
  const { activeCompany } = useActiveCompany()
  useEffect(() => {
    if (isOpen) setCompanyId(activeCompany?.id ?? null)
  }, [isOpen, activeCompany])

  // recipient choice matters only when the user runs more than one company
  const { data: companies } = useQuery({
    queryKey: ["companies", "upload-picker"],
    queryFn: async () =>
      (await CompaniesService.listCompanies({ query: { limit: 200 } })).data,
    enabled: isOpen,
  })
  const ownCompanies = companies?.data ?? []

  const mutation = useMutation({
    mutationFn: (selected: File) =>
      InvoicesService.uploadInvoice({
        body: { file: selected, recipient_company_id: companyId ?? null },
      }),
    onSuccess: (response) => {
      showSuccessToast(t("upload.toastUploaded"))
      setIsOpen(false)
      applyFile(null)
      setCompanyId(null)
      // reflect the new document in every open list immediately
      queryClient.invalidateQueries({ queryKey: ["invoices"] })
      queryClient.invalidateQueries({ queryKey: ["queue"] })
      navigate({
        to: "/invoices/$invoiceId",
        params: { invoiceId: response.data.id },
      })
    },
    onError: handleError.bind(showErrorToast),
  })

  const pickedFile = (input: HTMLInputElement) => {
    if (input.files?.length) {
      applyFile(input.files[0])
      stopCamera()
    }
  }

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        setIsOpen(open)
        if (!open) {
          stopCamera()
          applyFile(null)
          setCompanyId(null)
          setNaps2Profiles(null)
          setScannerPort(null)
        }
      }}
    >
      <Button
        onClick={() => setIsOpen(true)}
        data-testid="upload-invoice-button"
      >
        <Upload className="mr-2 size-4" />
        {t("upload.button")}
      </Button>
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("upload.title")}</DialogTitle>
          <DialogDescription>{t("upload.description")}</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto py-4">
          <div
            className={cn(
              "grid gap-4",
              (captureMode !== null || file !== null) && "md:grid-cols-2",
            )}
          >
            {/* the document, large and to the left; stacked last on phones */}
            {captureMode !== null ? (
              <div
                className="order-2 grid gap-2 md:order-first"
                data-testid="invoice-camera-preview"
              >
                <video
                  ref={(element) => {
                    videoRef.current = element
                    if (element) element.srcObject = streamRef.current
                  }}
                  autoPlay
                  muted
                  playsInline
                  className="max-h-[60vh] w-full rounded-md border bg-black object-contain"
                  onPlaying={() => setCameraReady(true)}
                  onPause={() => setCameraReady(false)}
                  onStalled={() => setCameraReady(false)}
                />
              </div>
            ) : (
              file && (
                <div
                  className="order-2 grid gap-2 md:order-first"
                  data-testid="invoice-upload-preview"
                >
                  {previewUrl ? (
                    <img
                      src={previewUrl}
                      alt=""
                      className="max-h-[60vh] w-full rounded-md border bg-white object-contain"
                      data-testid="invoice-upload-preview-image"
                    />
                  ) : (
                    <p className="text-sm text-muted-foreground">{file.name}</p>
                  )}
                </div>
              )
            )}
            <div className="order-1 grid content-start gap-2 md:order-none">
              <Label htmlFor="invoice-file">{t("upload.file")}</Label>
              <Input
                id="invoice-file"
                data-testid="invoice-file-input"
                type="file"
                accept=".pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff"
                ref={fileInputRef}
                disabled={captureMode !== null}
                onInput={(e) => pickedFile(e.target as HTMLInputElement)}
              />
              {captureMode === null && naps2Profiles === null && (
                <div className="flex gap-2">
                  {cameraAvailable && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      data-testid="invoice-camera-button"
                      onClick={() => startCamera("photo")}
                    >
                      <Camera className="mr-2 size-4" />
                      {t("upload.cameraButton")}
                    </Button>
                  )}
                  <LoadingButton
                    type="button"
                    variant="outline"
                    size="sm"
                    loading={scanning}
                    data-testid="invoice-scan-button"
                    onClick={handleScanClick}
                  >
                    <ScanLine className="mr-2 size-4" />
                    {t("upload.scanButton")}
                  </LoadingButton>
                </div>
              )}
              {captureMode === null &&
                naps2Profiles !== null &&
                naps2Profiles.length > 0 && (
                  <div className="grid gap-2" data-testid="naps2-panel">
                    <Label>{t("upload.naps2Profiles")}</Label>
                    <Select
                      value={scannerPort ?? undefined}
                      onValueChange={setScannerPort}
                    >
                      <SelectTrigger data-testid="scanner-profile-select">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {naps2Profiles.map((profile) => (
                          <SelectItem
                            key={profile.port}
                            value={String(profile.port)}
                          >
                            {profile.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <label
                      htmlFor="invoice-auto-trim-input"
                      className="flex items-center gap-2 text-sm"
                    >
                      <Checkbox
                        id="invoice-auto-trim-input"
                        checked={autoTrim}
                        onCheckedChange={(checked) =>
                          toggleAutoTrim(checked === true)
                        }
                        data-testid="invoice-auto-trim"
                      />
                      {t("upload.autoTrim")}
                    </label>
                    <div className="flex gap-2">
                      <LoadingButton
                        type="button"
                        size="sm"
                        loading={scanning}
                        data-testid="scanner-scan-button"
                        disabled={!scannerPort}
                        onClick={runNaps2Scan}
                      >
                        {t("upload.scanButton")}
                      </LoadingButton>
                      {cameraAvailable && (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          data-testid="scanner-camera-fallback"
                          onClick={() => startCamera("scan")}
                        >
                          <Camera className="mr-1 size-4" />
                          {t("upload.cameraButton")}
                        </Button>
                      )}
                    </div>
                  </div>
                )}
              {captureMode !== null && (
                <>
                  {captureMode === "scan" && (
                    <label
                      htmlFor="invoice-auto-trim-input"
                      className="flex items-center gap-2 text-sm"
                    >
                      <Checkbox
                        id="invoice-auto-trim-input"
                        checked={autoTrim}
                        onCheckedChange={(checked) =>
                          toggleAutoTrim(checked === true)
                        }
                        data-testid="invoice-auto-trim"
                      />
                      {t("upload.autoTrim")}
                    </label>
                  )}
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      data-testid="invoice-camera-capture"
                      disabled={!cameraReady}
                      onClick={() => capturePhoto(captureMode, autoTrim)}
                    >
                      {captureMode === "scan"
                        ? t("upload.scanButton")
                        : t("upload.cameraTake")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={stopCamera}
                    >
                      <X className="mr-1 size-4" />
                      {t("upload.cameraRetry")}
                    </Button>
                  </div>
                </>
              )}
              {/* native camera entry point on phones, hidden otherwise */}
              <input
                ref={captureInputRef}
                type="file"
                accept="image/*"
                capture="environment"
                className="hidden"
                data-testid="invoice-capture-input"
                onInput={(e) => pickedFile(e.target as HTMLInputElement)}
              />
              {ownCompanies.length > 1 && (
                <div className="grid gap-2 pt-2">
                  <Label>{t("upload.recipient")}</Label>
                  <Select
                    value={companyId ?? undefined}
                    onValueChange={setCompanyId}
                  >
                    <SelectTrigger data-testid="upload-recipient-select">
                      <SelectValue
                        placeholder={t("upload.recipientPlaceholder")}
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {ownCompanies.map((company) => (
                        <SelectItem key={company.id} value={company.id}>
                          {company.name} ({company.ico})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    {t("upload.recipientHint")}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
        <DialogFooter className="shrink-0">
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              {t("common.cancel")}
            </Button>
          </DialogClose>
          <LoadingButton
            type="button"
            loading={mutation.isPending}
            disabled={!file}
            data-testid="upload-invoice-confirm-button"
            onClick={() => file && mutation.mutate(file)}
          >
            {t("upload.upload")}
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default UploadInvoice
