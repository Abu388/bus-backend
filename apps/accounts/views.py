from django.db.models import Q
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import CustomUser
from .serializers import (
    LoginInputSerializer,
    LoginResponseSerializer,
    RegisterSerializer,
    UserProfileSerializer,
)


@extend_schema(
    request=RegisterSerializer,
    responses={
        201: OpenApiResponse(description="User registered successfully"),
        400: OpenApiResponse(description="Registration failed. Validation errors are returned."),
    },
    description="Register a new user",
    summary="User Registration",
    tags=["Authentication"],
)
@api_view(['POST'])
@permission_classes([AllowAny])
def register_user(request):
    serializer = RegisterSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response(
            {
                "message": "User registered successfully",
                "user": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )
    return Response(
        {"message": "Failed to register user", "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


class CustomLoginView(APIView):
    permission_classes = [AllowAny]
    serializer_class = LoginInputSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="Login with email, phone, or username (returns JWT access, refresh, and user profile)",
        request=LoginInputSerializer,
        responses={200: LoginResponseSerializer},
    )
    def post(self, request):
        serializer = LoginInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        raw_id = (
            data.get('identifier')
            or data.get('email')
            or data.get('phone')
            or data.get('username')
            or ''
        ).strip()
        password = data.get('password', '')

        if not raw_id or not password:
            raise AuthenticationFailed("Identifier (email, phone, or username) and password are required.")

        user = CustomUser.objects.filter(
            Q(email__iexact=raw_id) | Q(phone=raw_id) | Q(username__iexact=raw_id)
        ).first()

        if not user or not user.check_password(password):
            raise AuthenticationFailed("Invalid credentials.")
        if not user.is_active:
            raise AuthenticationFailed("User account is suspended or inactive.")

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserProfileSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    @extend_schema(
        tags=["Authentication"],
        summary="Get currently authenticated user profile",
        responses={200: UserProfileSerializer},
    )
    def get(self, request):
        return Response(UserProfileSerializer(request.user).data, status=status.HTTP_200_OK)